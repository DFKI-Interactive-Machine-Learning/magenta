# Copyright 2019 The Magenta Authors.
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

"""SketchRNN data loading and image manipulation utilities."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import random
from queue import Queue

import numpy as np


def get_bounds(data, factor=10):
  """Return bounds of data."""
  min_x = 0
  max_x = 0
  min_y = 0
  max_y = 0

  abs_x = 0
  abs_y = 0
  for i in range(len(data)):
    x = float(data[i, 0]) / factor
    y = float(data[i, 1]) / factor
    abs_x += x
    abs_y += y
    min_x = min(min_x, abs_x)
    min_y = min(min_y, abs_y)
    max_x = max(max_x, abs_x)
    max_y = max(max_y, abs_y)

  return (min_x, max_x, min_y, max_y)


def slerp(p0, p1, t):
  """Spherical interpolation."""
  omega = np.arccos(np.dot(p0 / np.linalg.norm(p0), p1 / np.linalg.norm(p1)))
  so = np.sin(omega)
  return np.sin((1.0 - t) * omega) / so * p0 + np.sin(t * omega) / so * p1


def lerp(p0, p1, t):
  """Linear interpolation."""
  return (1.0 - t) * p0 + t * p1


# A note on formats:
# Sketches are encoded as a sequence of strokes. stroke-3 and stroke-5 are
# different stroke encodings.
#   stroke-3 uses 3-tuples, consisting of x-offset, y-offset, and a binary
#       variable which is 1 if the pen is lifted between this position and
#       the next, and 0 otherwise.
#   stroke-5 consists of x-offset, y-offset, and p_1, p_2, p_3, a binary
#   one-hot vector of 3 possible pen states: pen down, pen up, end of sketch.
#   See section 3.1 of https://arxiv.org/abs/1704.03477 for more detail.
# Sketch-RNN takes input in stroke-5 format, with sketches padded to a common
# maximum length and prefixed by the special start token [0, 0, 1, 0, 0]
# The QuickDraw dataset is stored using stroke-3.
def strokes_to_lines(strokes):
  """Convert stroke-3 format to polyline format."""
  x = 0
  y = 0
  lines = []
  line = []
  for i in range(len(strokes)):
    if strokes[i, 2] == 1:
      x += float(strokes[i, 0])
      y += float(strokes[i, 1])
      line.append([x, y])
      lines.append(line)
      line = []
    else:
      x += float(strokes[i, 0])
      y += float(strokes[i, 1])
      line.append([x, y])
  return lines


def lines_to_strokes(lines):
  """Convert polyline format to stroke-3 format."""
  eos = 0
  strokes = [[0, 0, 0]]
  for line in lines:
    linelen = len(line)
    for i in range(linelen):
      eos = 0 if i < linelen - 1 else 1
      strokes.append([line[i][0], line[i][1], eos])
  strokes = np.array(strokes)
  strokes[1:, 0:2] -= strokes[:-1, 0:2]
  return strokes[1:, :]


def augment_strokes(strokes, prob=0.0):
  """Perform data augmentation by randomly dropping out strokes."""
  # drop each point within a line segments with a probability of prob
  # note that the logic in the loop prevents points at the ends to be dropped.
  result = []
  prev_stroke = [0, 0, 1]
  count = 0
  stroke = [0, 0, 1]  # Added to be safe.
  for i in range(len(strokes)):
    candidate = [strokes[i][0], strokes[i][1], strokes[i][2]]
    if candidate[2] == 1 or prev_stroke[2] == 1:
      count = 0
    else:
      count += 1
    urnd = np.random.rand()  # uniform random variable
    if candidate[2] == 0 and prev_stroke[2] == 0 and count > 2 and urnd < prob:
      stroke[0] += candidate[0]
      stroke[1] += candidate[1]
    else:
      stroke = candidate
      prev_stroke = stroke
      result.append(stroke)
  return np.array(result)


def scale_bound(stroke, average_dimension=10.0):
  """Scale an entire image to be less than a certain size."""
  # stroke is a numpy array of [dx, dy, pstate], average_dimension is a float.
  # modifies stroke directly.
  bounds = get_bounds(stroke, 1)
  max_dimension = max(bounds[1] - bounds[0], bounds[3] - bounds[2])
  stroke[:, 0:2] /= (max_dimension / average_dimension)


def to_normal_strokes(big_stroke):
  """Convert from stroke-5 format (from sketch-rnn paper) back to stroke-3."""
  l = 0
  for i in range(len(big_stroke)):
    if big_stroke[i, 4] > 0:
      l = i
      break
  if l == 0:
    l = len(big_stroke)
  result = np.zeros((l, 3))
  result[:, 0:2] = big_stroke[0:l, 0:2]
  result[:, 2] = big_stroke[0:l, 3]
  return result


def clean_strokes(sample_strokes, factor=100):
  """Cut irrelevant end points, scale to pixel space and store as integer."""
  # Useful function for exporting data to .json format.
  copy_stroke = []
  added_final = False
  for j in range(len(sample_strokes)):
    finish_flag = int(sample_strokes[j][4])
    if finish_flag == 0:
      copy_stroke.append([
          int(round(sample_strokes[j][0] * factor)),
          int(round(sample_strokes[j][1] * factor)),
          int(sample_strokes[j][2]),
          int(sample_strokes[j][3]), finish_flag
      ])
    else:
      copy_stroke.append([0, 0, 0, 0, 1])
      added_final = True
      break
  if not added_final:
    copy_stroke.append([0, 0, 0, 0, 1])
  return copy_stroke


def to_big_strokes(stroke, max_len=250):
  """Converts from stroke-3 to stroke-5 format and pads to given length."""
  # (But does not insert special start token).

  result = np.zeros((max_len, 5), dtype=float)
  l = len(stroke)
  assert l <= max_len
  result[0:l, 0:2] = stroke[:, 0:2]
  result[0:l, 3] = stroke[:, 2]
  result[0:l, 2] = 1 - result[0:l, 3]
  result[l:, 4] = 1
  return result


def get_max_len(sketches):
  """[InkRNN] Return the maximum stroke length within an array of sketches."""
  max_len = 0
  for sketch in sketches:
    ml = get_max_stroke_len(sketch)
    if ml > max_len:
      max_len = ml
  return max_len


def get_max_stroke_len(sketch):
  """[InkRNN] Return the maximum stroke length of a single sketch."""
  return max([len(stroke) for stroke in split_sketch(sketch)])


def split_sketch(data):
  """[InkRNN] splits a sketch in stroke-3 format into individual pen strokes (as sub-sketches)"""
  pen_stroke_bounds = np.where(data[:, 2] == 1)[0] + 1  # last dot of a stroke is marked with one
  pen_strokes = np.split(data, pen_stroke_bounds[:-1])
  return pen_strokes


class DataLoader(object):
  """Class for loading data."""

  def __init__(self,
               strokes,
               batch_size=100,
               max_seq_length=250,
               scale_factor=1.0,
               random_scale_factor=0.0,
               augment_stroke_prob=0.0,
               limit=1000):
    self.stroke_batch_queue = Queue()
    self.batch_size = batch_size  # minibatch size
    self.max_seq_length = max_seq_length  # N_max in sketch-rnn paper
    self.scale_factor = scale_factor  # divide offsets by this factor
    self.random_scale_factor = random_scale_factor  # data augmentation method
    # Removes large gaps in the data. x and y offsets are clamped to have
    # absolute value no greater than this limit.
    self.limit = limit
    self.augment_stroke_prob = augment_stroke_prob  # data augmentation method
    self.start_stroke_token = [0, 0, 1, 0, 0]  # S_0 in sketch-rnn paper
    # sets self.strokes (list of ndarrays, one per sketch, in stroke-3 format,
    # sorted by size)
    self.preprocess(strokes)

  def preprocess(self, sketches):
    """
    * Remove entries from strokes having > max_seq_length points.
    * Scale x and y deltas with normalization factor
    * Sort sketches by total amount of dots -> self.strokes
    """
    raw_data = []
    seq_len = []
    count_data = 0

    for i in range(len(sketches)):
      sketch = sketches[i]
      if get_max_stroke_len(sketch) <= (self.max_seq_length):  # len(sketch) cannot be used for InkRNN
        count_data += 1
        # removes large gaps from the data
        sketch = np.minimum(sketch, self.limit)
        sketch = np.maximum(sketch, -self.limit)
        sketch = np.array(sketch, dtype=np.float32)
        sketch[:, 0:2] /= self.scale_factor
        raw_data.append(sketch)
        seq_len.append(len(sketch))
    seq_len = np.array(seq_len)  # nstrokes for each sketch
    idx = np.argsort(seq_len)
    self.strokes = []
    for i in range(len(seq_len)):
      self.strokes.append(raw_data[idx[i]])
    print("total images <= max_seq_len is %d" % count_data)
    self.num_batches = int(count_data / self.batch_size)

  def random_sample(self):
    """Return a random sample, in stroke-3 format as used by draw_strokes."""
    sample = np.copy(random.choice(self.strokes))
    return sample

  def random_scale(self, data):
    """Augment data by stretching x and y axis randomly [1-e, 1+e]."""
    x_scale_factor = (
        np.random.random() - 0.5) * 2 * self.random_scale_factor + 1.0
    y_scale_factor = (
        np.random.random() - 0.5) * 2 * self.random_scale_factor + 1.0
    result = np.copy(data)
    result[:, 0] *= x_scale_factor
    result[:, 1] *= y_scale_factor
    return result

  def calculate_normalizing_scale_factor(self):
    """Calculate the normalizing factor explained in appendix of sketch-rnn."""
    data = []
    for i in range(len(self.strokes)):
      if get_max_stroke_len(self.strokes[i]) > self.max_seq_length:  # len(self.strokes[i]) cannot be used with InkRNN
        continue
      for j in range(len(self.strokes[i])):
        data.append(self.strokes[i][j, 0])
        data.append(self.strokes[i][j, 1])
    data = np.array(data)
    return np.std(data)

  def normalize(self, scale_factor=None):
    """Normalize entire dataset (delta_x, delta_y) by the scaling factor."""
    if scale_factor is None:
      scale_factor = self.calculate_normalizing_scale_factor()
    self.scale_factor = scale_factor
    for i in range(len(self.strokes)):
      self.strokes[i][:, 0:2] /= self.scale_factor

  def _get_batch_from_indices(self, indices):
    """Given a list of indices, return the potentially augmented batch."""
    x_batch = []
    seq_len = []
    for idx in range(len(indices)):
      i = indices[idx]
      data = self.random_scale(self.strokes[i])
      data_copy = np.copy(data)
      if self.augment_stroke_prob > 0:
        data_copy = augment_strokes(data_copy, self.augment_stroke_prob)
      x_batch.append(data_copy)
      length = len(data_copy)
      seq_len.append(length)
    seq_len = np.array(seq_len, dtype=int)
    # We return three things: stroke-3 format, stroke-5 format, list of seq_len.
    return x_batch, self.pad_batch(x_batch, self.max_seq_length), seq_len

  def random_batch(self):
    """Return a randomised portion of the training data."""
    raise NotImplementedError("This is the SketchRNN batch generation which is not supported/tested in this version.")
    idx = np.random.permutation(range(0, len(self.strokes)))[0:self.batch_size]
    return self._get_batch_from_indices(idx)

  def _get_sketches_from_indices(self, indices):
    """[InkRNN] Given a list of indices, return the potentially augmented sketches."""
    sketches = []
    num_strokes = []
    for idx in range(len(indices)):
      i = indices[idx]
      data = self.random_scale(self.strokes[i])
      data_copy = np.copy(data)
      if self.augment_stroke_prob > 0:
        data_copy = augment_strokes(data_copy, self.augment_stroke_prob)
      sketches.append(data_copy)
      length = np.sum(data_copy[:, 2])
      num_strokes.append(length)
    num_strokes = np.array(num_strokes, dtype=int)

    return sketches, num_strokes

  def pad_stroke_batch(self, sketch_strokes):
    """
    [InkRNN] Build k padded mini-batches in stroke-5 format, k = max(number of strokes per sketch).
      - first mini-batch includes the first strokes of all sketches with preceding s_0=100;
      - pad with 010, if there is a subsequent stroke
      - pad with 001, if there is no subsequent stroke
      - further mini-batches include the 2nd, 3rd, ..., jth stroke of each sketch, up to k
      - TODO: (?do not?) add s_0 for non-leading strokes: the x, y deltas might be lost -> test it
      - if there is no further stroke for some sketches, but k is not reached: add 001 vectors to the batch
    """
    max_num_strokes = max([len(sketch) for sketch in sketch_strokes])

    batches = []
    for j in range(0, max_num_strokes):
      result = np.zeros((self.batch_size, self.max_seq_length + 1, 5), dtype=float)
      seq_len = []

      for i in range(0, self.batch_size):
        # if there is no further stroke for sketch i
        if len(sketch_strokes[i]) <= j:
          result[i, :, 4] = 1  # pad with [00001]s, see [Kaiyrbekov & Sezgin 2019]
          seq_len.append(0)
        # if there is at least one further stroke for sketch i
        else:
          l = len(sketch_strokes[i][j])
          seq_len.append(l)
          assert l <= self.max_seq_length
          # set stroke-5 data
          result[i, 0:l, 0:2] = sketch_strokes[i][j][:, 0:2]
          result[i, 0:l, 3] = sketch_strokes[i][j][:, 2]
          result[i, 0:l, 2] = 1 - result[i, 0:l, 3]

          # pad stroke depending on their position in the sketch;
          is_last_stroke = len(sketch_strokes[i]) <= j + 1
          if is_last_stroke:
            result[i, l:, 4] = 1  # set end of sketch bit
          else:
            result[i, l:, 3] = 1  # set end of stroke bit

          # s_0 prefix policy: first stroke only <-> all stokes
          all_strokes_policy = True
          if j == 0 or all_strokes_policy:
            # shift stroke signal by 1
            result[i, 1:, :] = result[i, :-1, :]
            # set first signal sample to s_0
            result[i, 0, :] = 0
            result[i, 0, 2] = self.start_stroke_token[2]  # setting S_0 from paper.
            result[i, 0, 3] = self.start_stroke_token[3]
            result[i, 0, 4] = self.start_stroke_token[4]
            # increase sequence length by one, due to prepended s_0
            seq_len[-1] += 1

      batches.append((None, result, seq_len))
    return batches

  def random_stroke_batch(self):
    """[InkRNN] Return a mini-batch from the training data as described in [Kaiyrbekov and Sezgin 2019] section 3.1."""

    if self.stroke_batch_queue.empty():
      # Fill the queue with the next set of batches as described in [Kaiyrbekov and Sezgin 2019] section 3.1.

      # Randomly select n sketches from the training data (n=batch_size)
      idx = np.random.permutation(range(0, len(self.strokes)))[0:self.batch_size]
      sketches, num_strokes = self._get_sketches_from_indices(idx)
      # Split all sketches into strokes
      sketch_strokes = [split_sketch(sketch) for sketch in sketches]
      for batch in self.pad_stroke_batch(sketch_strokes):
        self.stroke_batch_queue.put(batch, block=False)
    assert not self.stroke_batch_queue.empty()
    return self.stroke_batch_queue.get(block=False)

  def get_stroke_batches(self, idx):
    """[InkRNN] Generate stroke-wise mini-batches using all available sketches"""
    # Select n sketches for the idx_th batch (n=batch_size)
    start_idx = idx * self.batch_size
    indices = range(start_idx, start_idx + self.batch_size)
    sketches, num_strokes = self._get_sketches_from_indices(indices)
    # Split all sketches into strokes
    sketch_strokes = [split_sketch(sketch) for sketch in sketches]
    return self.pad_stroke_batch(sketch_strokes)

  def get_batch(self, idx):
    """Get the idx'th batch from the dataset."""
    raise NotImplementedError("This is the SketchRNN batch generation which is not supported/tested in this version.")
    assert idx >= 0, "idx must be non negative"
    # assert idx < self.num_batches, "idx must be less than the number of batches"
    start_idx = idx * self.batch_size
    indices = range(start_idx, start_idx + self.batch_size)
    return self._get_batch_from_indices(indices)

  def pad_batch(self, batch, max_len):
    """Pad the batch to be stroke-5 bigger format as described in paper."""
    result = np.zeros((self.batch_size, max_len + 1, 5), dtype=float)
    assert len(batch) == self.batch_size
    for i in range(self.batch_size):
      l = len(batch[i])
      assert l <= max_len
      result[i, 0:l, 0:2] = batch[i][:, 0:2]
      result[i, 0:l, 3] = batch[i][:, 2]
      result[i, 0:l, 2] = 1 - result[i, 0:l, 3]
      result[i, l:, 4] = 1
      # put in the first token, as described in sketch-rnn methodology
      result[i, 1:, :] = result[i, :-1, :]
      result[i, 0, :] = 0
      result[i, 0, 2] = self.start_stroke_token[2]  # setting S_0 from paper.
      result[i, 0, 3] = self.start_stroke_token[3]
      result[i, 0, 4] = self.start_stroke_token[4]
    return result
