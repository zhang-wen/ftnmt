# coding=utf-8
# Copyright 2017 The Tensor2Tensor Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Encoders for text data.

* TextEncoder: base class
* ByteTextEncoder: for ascii text
* TokenTextEncoder: with user-supplied vocabulary file
* SubwordTextEncoder: invertible
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
from itertools import chain
import re
import sys

# Dependency imports
import six
from six.moves import xrange  # pylint: disable=redefined-builtin

import tools.tokenizer as tokenizer
from tools.utils import *

if six.PY2:
  RESERVED_TOKENS_BYTES = RESERVED_TOKENS
else:
  RESERVED_TOKENS_BYTES = [bytes(PAD_WORD, "ascii"), bytes[BOS_WORD, "ascii"],bytes(EOS_WORD, "ascii")]

# Regular expression for unescaping token strings.
# '\u' is converted to '_'
# '\\' is converted to '\'
# '\213;' is converted to unichr(213)
_UNESCAPE_REGEX = re.compile(r"\\u|\\\\|\\([0-9]+);")
_ESCAPE_CHARS = set(u"\\_u;0123456789")


# Conversion between Unicode and UTF-8, if required (on Python2).
if six.PY2:

  def native_to_unicode(s):
    return s if isinstance(s, unicode) else s.decode("utf8")

  def unicode_to_native(s):
    return s.encode("utf-8")
else:  # No conversion required on Python >= 3.

  def native_to_unicode(s):
    return s

  def unicode_to_native(s):
    return s


class TextEncoder(object):
  """Base class for converting from ints to/from human readable strings."""

  def __init__(self, num_reserved_ids=NUM_RESERVED_TOKENS):
    self._num_reserved_ids = num_reserved_ids

  @property
  def num_reserved_ids(self):
    return self._num_reserved_ids

  def encode(self, s):
    """Transform a human-readable string into a sequence of int ids.

    The ids should be in the range [num_reserved_ids, vocab_size). Ids [0,
    num_reserved_ids) are reserved.

    EOS is not appended.

    Args:
      s: human-readable string to be converted.

    Returns:
      ids: list of integers
    """
    return [int(w) + self._num_reserved_ids for w in s.split()]

  def decode(self, ids):
    """Transform a sequence of int ids into a human-readable string.

    EOS is not expected in ids.

    Args:
      ids: list of integers to be converted.

    Returns:
      s: human-readable string.
    """
    return " ".join(self.decode_list(ids))

  def decode_list(self, ids):
    """Transform a sequence of int ids into a their string versions.

    This method supports transforming individual input/output ids to their
    string versions so that sequence to/from text conversions can be visualized
    in a human readable format.

    Args:
      ids: list of integers to be converted.

    Returns:
      strs: list of human-readable string.
    """
    decoded_ids = []
    for id_ in ids:
      if 0 <= id_ < self._num_reserved_ids:
        decoded_ids.append(RESERVED_TOKENS[int(id_)])
      else:
        decoded_ids.append(id_ - self._num_reserved_ids)
    return [str(d) for d in decoded_ids]

  @property
  def vocab_size(self):
    raise NotImplementedError()


class ByteTextEncoder(TextEncoder):
  """Encodes each byte to an id. For 8-bit strings only."""

  def encode(self, s):
    numres = self._num_reserved_ids
    if six.PY2:
      if isinstance(s, unicode):
        s = s.encode("utf-8")
      return [ord(c) + numres for c in s]
    # Python3: explicitly convert to UTF-8
    return [c + numres for c in s.encode("utf-8")]

  def decode(self, ids):
    numres = self._num_reserved_ids
    decoded_ids = []
    int2byte = six.int2byte
    for id_ in ids:
      if 0 <= id_ < numres:
        decoded_ids.append(RESERVED_TOKENS_BYTES[int(id_)])
      else:
        decoded_ids.append(int2byte(id_ - numres))
    if six.PY2:
      return "".join(decoded_ids)
    # Python3: join byte arrays and then decode string
    return b"".join(decoded_ids).decode("utf-8", "replace")

  def decode_list(self, ids):
    numres = self._num_reserved_ids
    decoded_ids = []
    int2byte = six.int2byte
    for id_ in ids:
      if 0 <= id_ < numres:
        decoded_ids.append(RESERVED_TOKENS_BYTES[int(id_)])
      else:
        decoded_ids.append(int2byte(id_ - numres))
    # Python3: join byte arrays and then decode string
    return decoded_ids

  @property
  def vocab_size(self):
    return 2**8 + self._num_reserved_ids

class TokenTextEncoder(TextEncoder):
  """Encoder based on a user-supplied vocabulary (file or list)."""

  def __init__(self,
               vocab_filename,
               reverse=False,
               vocab_list=None,
               replace_oov=None,
               num_reserved_ids=NUM_RESERVED_TOKENS):
    """Initialize from a file or list, one token per line.

    Handling of reserved tokens works as follows:
    - When initializing from a list, we add reserved tokens to the vocab.
    - When initializing from a file, we do not add reserved tokens to the vocab.
    - When saving vocab files, we save reserved tokens to the file.

    Args:
      vocab_filename: If not None, the full filename to read vocab from. If this
         is not None, then vocab_list should be None.
      reverse: Boolean indicating if tokens should be reversed during encoding
         and decoding.
      vocab_list: If not None, a list of elements of the vocabulary. If this is
         not None, then vocab_filename should be None.
      replace_oov: If not None, every out-of-vocabulary token seen when
         encoding will be replaced by this string (which must be in vocab).
      num_reserved_ids: Number of IDs to save for reserved tokens like <EOS>.
    """
    super(TokenTextEncoder, self).__init__(num_reserved_ids=num_reserved_ids)
    self._reverse = reverse
    self._replace_oov = replace_oov
    if vocab_filename:
      self._init_vocab_from_file(vocab_filename)
    else:
      assert vocab_list is not None
      self._init_vocab_from_list(vocab_list)

  def encode(self, sentence):
    """Converts a space-separated string of tokens to a list of ids."""
    tokens = sentence.strip().split()
    if self._replace_oov is not None:
      tokens = [t if t in self._token_to_id else self._replace_oov
                for t in tokens]
    ret = [self._token_to_id[tok] for tok in tokens]
    return ret[::-1] if self._reverse else ret

  def decode(self, ids):
    return " ".join(self.decode_list(ids))

  def decode_list(self, ids):
    seq = reversed(ids) if self._reverse else ids
    return [self._safe_id_to_token(i) for i in seq]

  @property
  def vocab_size(self):
    return len(self._id_to_token)

  def _safe_id_to_token(self, idx):
    return self._id_to_token.get(idx, "ID_%d" % idx)

  def _init_vocab_from_file(self, filename):
    """Load vocab from a file.

    Args:
      filename: The file to load vocabulary from.
    """
    def token_gen():
      with open(filename) as f:
        for line in f:
          token = line.strip()
          yield token

    self._init_vocab(token_gen(), add_reserved_tokens=False)

  def _init_vocab_from_list(self, vocab_list):
    """Initialize tokens from a list of tokens.

    It is ok if reserved tokens appear in the vocab list. They will be
    removed. The set of tokens in vocab_list should be unique.

    Args:
      vocab_list: A list of tokens.
    """
    def token_gen():
      for token in vocab_list:
        if token not in RESERVED_TOKENS:
          yield token

    self._init_vocab(token_gen())

  def _init_vocab(self, token_generator, add_reserved_tokens=True):
    """Initialize vocabulary with tokens from token_generator."""

    self._id_to_token = {}
    non_reserved_start_index = 0

    if add_reserved_tokens:
      self._id_to_token.update(enumerate(RESERVED_TOKENS))
      non_reserved_start_index = len(RESERVED_TOKENS)

    self._id_to_token.update(
        enumerate(token_generator, start=non_reserved_start_index))

    # _token_to_id is the reverse of _id_to_token
    self._token_to_id = dict((v, k)
                             for k, v in six.iteritems(self._id_to_token))

  def store_to_file(self, filename):
    """Write vocab file to disk.

    Vocab files have one token per line. The file ends in a newline. Reserved
    tokens are written to the vocab file as well.

    Args:
      filename: Full path of the file to store the vocab to.
    """
    with open(filename, "w") as f:
      for i in xrange(len(self._id_to_token)):
        f.write(self._id_to_token[i] + "\n")


def _escape_token(token, alphabet):
  """Escape away underscores and OOV characters and append '_'.

  This allows the token to be experessed as the concatenation of a list
  of subtokens from the vocabulary. The underscore acts as a sentinel
  which allows us to invertibly concatenate multiple such lists.

  Args:
    token: A unicode string to be escaped.
    alphabet: A set of all characters in the vocabulary's alphabet.

  Returns:
    escaped_token: An escaped unicode string.

  Raises:
    ValueError: If the provided token is not unicode.
  """
  if not isinstance(token, six.text_type):
    raise ValueError("Expected string type for token, got %s" % type(token))

  token = token.replace(u"\\", u"\\\\").replace(u"_", u"\\u")
  ret = [c if c in alphabet and c != u"\n" else r"\%d;" % ord(c) for c in token]
  #wlog(ret)
  return u"".join(ret) + "_"


def _unescape_token(escaped_token):
  """Inverse of _escape_token().

  Args:
    escaped_token: a unicode string, such as '我要买它_' or now_

  Returns:
    token: a unicode string
  """

  def match(m):
    if m.group(1) is None:
      return u"_" if m.group(0) == u"\\u" else u"\\"

    try:
      return six.unichr(int(m.group(1)))
    except (ValueError, OverflowError) as _:
      return ""

  trimmed = escaped_token[:-1] if escaped_token.endswith("_") else escaped_token
  return _UNESCAPE_REGEX.sub(match, trimmed)


class SubwordTextEncoder(TextEncoder):
  """Class for invertibly encoding text using a limited vocabulary.

  Invertibly encodes a native string as a sequence of subtokens from a limited
  vocabulary.

  A SubwordTextEncoder is built from a corpus (so it is tailored to the text in
  the corpus), and stored to a file. See text_encoder_build_subword.py.

  It can then be loaded and used to encode/decode any text.

  Encoding has four phases:

  1. Tokenize into a list of tokens.  Each token is a unicode string of either
     all alphanumeric characters or all non-alphanumeric characters.  We drop
     tokens consisting of a single space that are between two alphanumeric
     tokens.

  2. Escape each token.  This escapes away special and out-of-vocabulary
     characters, and makes sure that each token ends with an underscore, and
     has no other underscores.

  3. Represent each escaped token as a the concatenation of a list of subtokens
     from the limited vocabulary.  Subtoken selection is done greedily from
     beginning to end.  That is, we construct the list in order, always picking
     the longest subtoken in our vocabulary that matches a prefix of the
     remaining portion of the encoded token.

  4. Concatenate these lists.  This concatenation is invertible due to the
     fact that the trailing underscores indicate when one list is finished.

  """

  def __init__(self, filename=None):
    """Initialize and read from a file, if provided.

    Args:
      filename: filename from which to read vocab. If None, do not load a
        vocab
    """
    self._alphabet = set()
    if filename is not None:
      self._load_from_file(filename)
    super(SubwordTextEncoder, self).__init__(num_reserved_ids=None)

  def encode(self, raw_text):
    """Converts a native string to a list of subtoken ids.

    Args:
      raw_text: a native string.
    Returns:
      a list of integers in the range [0, vocab_size)
    """
    return self._tokens_to_subtoken_ids(
        tokenizer.encode(native_to_unicode(raw_text)))

  def decode(self, subtokens):
    """Converts a sequence of subtoken ids to a native string.

    Args:
      subtokens: a list of integers in the range [0, vocab_size)
    Returns:
      a native string
    """
    #print('subtokens: ', subtokens)
    ret, trans_idx_to_subws = self._subtoken_ids_to_tokens(subtokens)
    #print('trans_idx_to_subws: ', trans_idx_to_subws)
    #print('ret: ', ret)
    # trans_idx_to_subws: [u'总之_', u' , _', u'非常_', u'好吃_']
    # ret: [u'总之', u' , ', u'非常', u'好吃']
    return unicode_to_native(tokenizer.decode(ret)), trans_idx_to_subws

  def decode_list(self, subtokens):
    return [self._subtoken_id_to_subtoken_string(s) for s in subtokens]

  @property
  def vocab_size(self):
    """The subtoken vocabulary size."""
    return len(self._all_subtoken_strings)

  def size(self):
    """The subtoken vocabulary size."""
    return len(self._all_subtoken_strings)

  def _tokens_to_subtoken_ids(self, tokens):
    """Converts a list of tokens to a list of subtoken ids.

    Args:
      tokens: a list of strings.
    Returns:
      a list of integers in the range [0, vocab_size)
    """
    ret = []
    for token in tokens:
      ret.extend(self._token_to_subtoken_ids(token))
    return ret

  def _token_to_subtoken_ids(self, token):
    """Converts token to a list of subtoken ids.

    Args:
      token: a string.
    Returns:
      a list of integers in the range [0, vocab_size)
    """
    #print(token)
    cache_location = hash(token) % self._cache_size
    cache_key, cache_value = self._cache[cache_location]
    if cache_key == token:
      return cache_value
    ret = self._escaped_token_to_subtoken_ids(_escape_token(token, self._alphabet))
    #print(ret)
    self._cache[cache_location] = (token, ret)
    return ret

  def _subtoken_ids_to_tokens(self, subtokens):
    """Converts a list of subtoken ids to a list of tokens.

    Args:
      subtokens: a list of integers in the range [0, vocab_size)
    Returns:
      a list of strings.
    """
    #print('_subtoken_ids_to_tokens..........')
    #for s in subtokens: print(self._subtoken_id_to_subtoken_string(s))
    trans_idx_to_subws = [self._subtoken_id_to_subtoken_string(s) for s in subtokens]
    concatenated = "".join(trans_idx_to_subws)
    #print('concatenated: ', concatenated)
    split = concatenated.split("_")
    ret = []
    for t in split:
      if t:
        unescaped = _unescape_token(t + "_")
        if unescaped:
          ret.append(unescaped)
    # ret: [u'一', u'跳', u'一', u'跳', u'的', u'痛']
    return ret, trans_idx_to_subws

  def _subtoken_id_to_subtoken_string(self, subtoken):
    """Converts a subtoken integer ID to a subtoken string."""
    if 0 <= subtoken < self.vocab_size:
      return self._all_subtoken_strings[subtoken]
    return u""

  def _escaped_token_to_subtoken_strings(self, escaped_token):
    """Converts an escaped token string to a list of subtoken strings.

    Args:
      escaped_token: An escaped token as a unicode string.
    Returns:
      A list of subtokens as unicode strings.
    """
    # NOTE: This algorithm is greedy; it won't necessarily produce the "best"
    # list of subtokens.
    ret = []
    start = 0
    token_len = len(escaped_token)
    while start < token_len:
      for end in xrange(
          min(token_len, start + self._max_subtoken_len), start, -1):
        subtoken = escaped_token[start:end]
        #print('ccccc', subtoken, start, end, token_len, start + self._max_subtoken_len)
        # from large to small, until find the subtoken, we always can find characters
        if subtoken in self._subtoken_string_to_id:
          ret.append(subtoken)
          start = end
          break

      else:  # Did not break
        # If there is no possible encoding of the escaped token then one of the
        # characters in the token is not in the alphabet. This should be
        # impossible and would be indicative of a bug.
        assert False, "Token substring not found in subtoken vocabulary."

    return ret

  def _escaped_token_to_subtoken_ids(self, escaped_token):
    """Converts an escaped token string to a list of subtoken IDs.

    Args:
      escaped_token: An escaped token as a unicode string.
    Returns:
      A list of subtoken IDs as integers.
    """
    #print('-------------', escaped_token)
    #for subtoken in self._escaped_token_to_subtoken_strings(escaped_token):
    #    print(subtoken)
    #    print(self._subtoken_string_to_id[subtoken])
    return [
        self._subtoken_string_to_id[subtoken]
        for subtoken in self._escaped_token_to_subtoken_strings(escaped_token)
        # [u'\u6211', u'\u8981', u'\u4e70', u'\u5b83', u'_'] or [u'n', u'o', u'w', u'_']
    ]

  @classmethod
  def build_to_target_size(cls,
                           target_size,
                           token_counts,
                           min_val,
                           max_val,
                           num_iterations=4):
    """Builds a SubwordTextEncoder that has `vocab_size` near `target_size`.

    Uses simple recursive binary search to find a minimum token count that most
    closely matches the `target_size`.

    Args:
      target_size: Desired vocab_size to approximate.
      token_counts: A dictionary of token counts, mapping string to int.
      min_val: An integer; lower bound for the minimum token count.
      max_val: An integer; upper bound for the minimum token count.
      num_iterations: An integer; how many iterations of refinement.

    Returns:
      A SubwordTextEncoder instance.

    Raises:
      ValueError: If `min_val` is greater than `max_val`.
    """
    if min_val > max_val:
      raise ValueError("Lower bound for the minimum token count "
                       "is greater than the upper bound.")
    if target_size < 1:
      raise ValueError("Target size must be positive.")

    def bisect(min_val, max_val):   # 1 - 1000
      """Bisection to find the right size."""
      present_count = (max_val + min_val) // 2
      #wlog('Trying min_count {}'.format(present_count))
      subtokenizer = cls()
      subtokenizer.build_from_token_counts(token_counts, present_count, num_iterations)

      # Being within 1% of the target size is ok.
      is_ok = abs(subtokenizer.vocab_size - target_size) * 100 < target_size
      #wlog('{}, {}, {}'.format(subtokenizer.vocab_size, target_size, is_ok))
      # If min_val == max_val, we can't do any better than this.
      if is_ok or min_val >= max_val or present_count < 2:
        return subtokenizer

      if subtokenizer.vocab_size > target_size:
        other_subtokenizer = bisect(present_count + 1, max_val)
      else:
        other_subtokenizer = bisect(min_val, present_count - 1)

      if other_subtokenizer is None:
        return subtokenizer

      if (abs(other_subtokenizer.vocab_size - target_size) <
          abs(subtokenizer.vocab_size - target_size)):
        return other_subtokenizer
      return subtokenizer

    return bisect(min_val, max_val)

  def build_from_token_counts(self,
                              token_counts,
                              min_count,
                              num_iterations=4,
                              num_reserved_ids=NUM_RESERVED_TOKENS):
    """Train a SubwordTextEncoder based on a dictionary of word counts.

    Args:
      token_counts: a dictionary of Unicode strings to int.
      min_count: an integer - discard subtokens with lower counts.
      num_iterations: an integer.  how many iterations of refinement.
      num_reserved_ids: an integer.  how many ids to reserve for special tokens.

    Raises:
      ValueError: if reserved is not 0 or len(RESERVED_TOKENS). In this case, it
        is not clear what the space is being reserved for, or when it will be
        filled in.
    """
    #wlog(token_counts)  # token dict {token: count}
    # Initialize the alphabet. Note, this must include reserved tokens or it can
    # result in encoding failures.
    if num_reserved_ids == NUM_RESERVED_TOKENS:
      alphabet_tokens = chain(six.iterkeys(token_counts),
                              [native_to_unicode(t) for t in RESERVED_TOKENS])
    elif num_reserved_ids == 0:
      alphabet_tokens = six.iterkeys(token_counts)
    else:
      raise ValueError("Unexpected value for reserved. What is being reserved?")

    self._init_alphabet_from_tokens(alphabet_tokens)

    #for c in self._alphabet: wlog(c)
    #self._alphabet is set of all alphabet in training data, token by unicode tokenizer
    # Bootstrap the initial list of subtokens with the characters from the
    # alphabet plus the escaping characters.
    self._init_subtokens_from_list(list(self._alphabet), reserved=num_reserved_ids)
    # add <pad> and <EOS>
    #wlog('------------')
    #for c in self._all_subtoken_strings: wlog(c)

    # We build iteratively.  On each iteration, we segment all the words,
    # then count the resulting potential subtokens, keeping the ones
    # with high enough counts for our new vocabulary.
    if min_count < 1: min_count = 1
    for i in xrange(num_iterations):
      wlog('Iteration {0}'.format(i))

      # Collect all substrings of the encoded token that break along current
      # subtoken boundaries.
      subtoken_counts = collections.defaultdict(int)
      for token, count in six.iteritems(token_counts):
        #print(token) # '我要买它' or 'now'
        escaped_token = _escape_token(token, self._alphabet)
        #print(escaped_token) # '我要买它_' or now_
        # \\ -> \\\\, \ -> \\, _ -> \u
        subtokens = self._escaped_token_to_subtoken_strings(escaped_token)
        # [u'\u6211', u'\u8981', u'\u4e70', u'\u5b83', u'_'] or [u'n', u'o', u'w', u'_']
        #print('aaaaa', subtokens)
        start = 0
        '''
            new_subtoken:
                我
                我要
                我要买
                我要买它
                我要买它_
                要
                要买
                要买它
                要买它_
                买
                买它
                买它_
                它
                它_
                _
            greedy search all sub-string
        '''
        for subtoken in subtokens:
          for end in xrange(start + 1, len(escaped_token) + 1):
            new_subtoken = escaped_token[start:end]
            #print(new_subtoken)
            subtoken_counts[new_subtoken] += count
          start += len(subtoken)

      # Array of sets of candidate subtoken strings, by length.
      # all subtokens which have same length are in one set
      len_to_subtoken_strings = []
      for subtoken_string, count in six.iteritems(subtoken_counts):
        lsub = len(subtoken_string)
        if count >= min_count:
          while len(len_to_subtoken_strings) <= lsub:
            len_to_subtoken_strings.append(set())
          len_to_subtoken_strings[lsub].add(subtoken_string)
      #print(len_to_subtoken_strings)
      '''
            [set(),
             set(),
             {'ab'},
             set(),
             set(),
             {'aaaaa', 'abbbb'},
             set(),
             set(),
             set(),
             set(),
             {'abadsfasfa'}]
      '''
      # Consider the candidates longest to shortest, so that if we accept
      # a longer subtoken string, we can decrement the counts of its prefixes.
      new_subtoken_strings = []
      for lsub in xrange(len(len_to_subtoken_strings) - 1, 0, -1):
        subtoken_strings = len_to_subtoken_strings[lsub]
        for subtoken_string in subtoken_strings:    # for one length
          count = subtoken_counts[subtoken_string]
          if count >= min_count:
            # Exclude alphabet tokens here, as they must be included later,
            # explicitly, regardless of count.
            if subtoken_string not in self._alphabet:
              new_subtoken_strings.append((count, subtoken_string))
            for l in xrange(1, lsub):   # decrement the counts of its prefixes
              subtoken_counts[subtoken_string[:l]] -= count

      # Include the alphabet explicitly to guarantee all strings are encodable.
      new_subtoken_strings.extend((subtoken_counts.get(a, 0), a)
                                  for a in self._alphabet)
      new_subtoken_strings.sort(reverse=True)

      # Reinitialize to the candidate vocabulary.
      new_subtoken_strings = [subtoken for _, subtoken in new_subtoken_strings]
      if num_reserved_ids == len(RESERVED_TOKENS):
        new_subtoken_strings = RESERVED_TOKENS + new_subtoken_strings
      elif num_reserved_ids == 0:
        pass
      else:
        raise ValueError("num_reserved_ids must be 0 or %d but was %d" %
                         NUM_RESERVED_TOKENS, num_reserved_ids)
      self._init_subtokens_from_list(new_subtoken_strings)
      # !!!!!!!!!!!!!!!!
      # finally update the self._subtoken_string_to_id in self._init_subtokens_from_list
      # will use self._subtoken_string_to_id to map words into indexes
      # !!!!!!!!!!!!!!!!
      wlog('vocab_size = {}'.format(self.vocab_size))

  def dump(self):
    """Debugging dump of the current subtoken vocabulary."""
    subtoken_strings = [(i, s)
                        for s, i in six.iteritems(self._subtoken_string_to_id)]
    print(u", ".join(u"{0} : '{1}'".format(i, s)
                     for i, s in sorted(subtoken_strings)))

  def _init_subtokens_from_list(self, subtoken_strings, reserved=0):
    """Initialize token information from a list of subtoken strings.

    Args:
      subtoken_strings: a list of subtokens
      reserved: number of spaces to save at the beginning for reserved tokens

    Raises:
      ValueError: if reserved is not 0 or len(RESERVED_TOKENS). In this case, it
        is not clear what the space is being reserved for, or when it will be
        filled in.
    """
    if reserved == 0:
      self._all_subtoken_strings = subtoken_strings
    elif reserved == len(RESERVED_TOKENS):
      self._all_subtoken_strings = RESERVED_TOKENS + subtoken_strings
    else:
      # TODO(dtarlow): or should we fall back to the previous behavior and
      # insert copies of "" for each reserved count?
      raise ValueError("Unexpected value for reserved. What is being reserved?")

    #wlog(self._all_subtoken_strings)

    # we remember the maximum length of any subtoken to avoid having to
    # check arbitrarily long strings.
    self._max_subtoken_len = max([len(s) for s in subtoken_strings])
    #wlog([len(s) for s in subtoken_strings])
    #wlog('_max_subtoken_len: {}'.format(self._max_subtoken_len))
    # have sorted by much to less {<pad>:0, <EOS>:1, ..., }
    self._subtoken_string_to_id = {
        s: i + reserved
        for i, s in enumerate(subtoken_strings) if s
    }
    #wlog('_subtoken_string_to_id: {}'.format(self._subtoken_string_to_id))
    # Initialize the cache to empty.
    self._cache_size = 2 ** 20
    self._cache = [(None, None)] * self._cache_size

  def _init_alphabet_from_tokens(self, tokens):
    """Initialize alphabet from an iterable of token or subtoken strings."""
    # Include all characters from all tokens in the alphabet to guarantee that
    # any token can be encoded. Additionally, include all escaping characters.
    self._alphabet = {c for token in tokens for c in token}
    self._alphabet |= _ESCAPE_CHARS

  def _load_from_file_object(self, f):
    """Load from a file object.

    Args:
      f: File object to load vocabulary from
    """
    subtoken_strings = []
    for line in f:
      s = line.strip()
      # Some vocab files wrap words in single quotes, but others don't
      if ((s.startswith("'") and s.endswith("'")) or
          (s.startswith("\"") and s.endswith("\""))):
        s = s[1:-1]
      subtoken_strings.append(native_to_unicode(s))
    self._init_subtokens_from_list(subtoken_strings)
    self._init_alphabet_from_tokens(subtoken_strings)

  def _load_from_file(self, filename):
    """Load from a file.

    Args:
      filename: Filename to load vocabulary from
    """
    with open(filename) as f:
      self._load_from_file_object(f)

  def store_to_file(self, filename, add_single_quotes=True):
    with open(filename, "w") as f:
      for subtoken_string in self._all_subtoken_strings:
        if add_single_quotes:
          f.write("'" + unicode_to_native(subtoken_string) + "'\n")
        else:
          f.write(unicode_to_native(subtoken_string) + "\n")
    with open(filename+'.txt', "w") as f:
        for s, i in six.iteritems(self._subtoken_string_to_id):
            f.write('{}:{}\n'.format(s, i))


