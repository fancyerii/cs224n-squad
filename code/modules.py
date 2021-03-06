# Copyright 2018 Stanford University
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This file contains some basic model components"""

import tensorflow as tf
from tensorflow.python.ops.rnn_cell import DropoutWrapper
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.ops import rnn_cell


class RNNEncoder(object):
    """
    General-purpose module to encode a sequence using a RNN.
    It feeds the input through a RNN and returns all the hidden states.

    Note: In lecture 8, we talked about how you might use a RNN as an "encoder"
    to get a single, fixed size vector representation of a sequence
    (e.g. by taking element-wise max of hidden states).
    Here, we're using the RNN as an "encoder" but we're not taking max;
    we're just returning all the hidden states. The terminology "encoder"
    still applies because we're getting a different "encoding" of each
    position in the sequence, and we'll use the encodings downstream in the model.

    This code uses a bidirectional GRU, but you could experiment with other types of RNN.
    """

    def __init__(self, hidden_size, keep_prob, num_layers=1, mode="GRU", name=None):
        """
        Inputs:
          hidden_size: int. Hidden size of the RNN
          keep_prob: Tensor containing a single scalar that is the keep probability (for dropout)
        """
        self.hidden_size = hidden_size
        self.keep_prob = keep_prob
        self.num_layers=num_layers
        self.mode = mode
        self.name=name
        self.rnn_cells_fw = []
        self.rnn_cells_bw = []
        for _ in range(num_layers):
            if self.mode == 'GRU':
                print("Using GRUs")
                self.rnn_cells_fw.append(DropoutWrapper(rnn_cell.GRUCell(self.hidden_size), input_keep_prob=self.keep_prob))
                self.rnn_cells_bw.append(DropoutWrapper(rnn_cell.GRUCell(self.hidden_size), input_keep_prob=self.keep_prob))
            elif self.mode == 'LSTM':
                print("Using LSTMs")
                self.rnn_cells_fw.append(DropoutWrapper(tf.contrib.rnn.BasicLSTMCell(self.hidden_size), input_keep_prob=self.keep_prob))
                self.rnn_cells_bw.append(DropoutWrapper(tf.contrib.rnn.BasicLSTMCell(self.hidden_size), input_keep_prob=self.keep_prob))
        print("RNN Encoder")


    def build_graph(self, inputs, masks):
        """
        Inputs:
          inputs: Tensor shape (batch_size, seq_len, input_size)
          masks: Tensor shape (batch_size, seq_len).
            Has 1s where there is real input, 0s where there's padding.
            This is used to make sure tf.nn.bidirectional_dynamic_rnn doesn't iterate through masked steps.

        Returns:
          out: Tensor shape (batch_size, seq_len, hidden_size*2).
            This is all hidden states (fw and bw hidden states are concatenated).
        """
        scope = self.name
        if scope is None:
            scope = "RNNEncoder"
        with vs.variable_scope(scope):
            input_lens = tf.reduce_sum(masks, reduction_indices=1) # shape (batch_size)
            # Note: fw_out and bw_out are the hidden states for every timestep.
            # # Each is shape (batch_size, seq_len, hidden_size).
            if self.num_layers==1:
                (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(
                    self.rnn_cells_fw[0], self.rnn_cells_bw[0],
                    inputs,
                    input_lens,
                    dtype=tf.float32)
                # Concatenate the forward and backward hidden states
                out = tf.concat([fw_out, bw_out], 2)
            else:
#                inputs_list = tf.unstack(inputs, axis=1)
#                tf.assert_equal(len(inputs_list),tf.shape(inputs)[1])
                out, _, _ = tf.contrib.rnn.stack_bidirectional_dynamic_rnn(
                  self.rnn_cells_fw,
                  self.rnn_cells_bw,
                  inputs,
                  sequence_length=input_lens,
                  dtype=tf.float32)
               # out = tf.stack(out, axis=1)
                tf.assert_equal(tf.shape(out), [tf.shape(inputs)[0], tf.shape(inputs)[1], 2 * self.hidden_size])
            out = tf.nn.dropout(out, self.keep_prob)

            return out


class SimpleSoftmaxLayer(object):
    """
    Module to take set of hidden states, (e.g. one for each context location),
    and return probability distribution over those states.
    """

    def __init__(self):
        pass

    def build_graph(self, inputs, masks):
        """
        Applies one linear downprojection layer, then softmax.

        Inputs:
          inputs: Tensor shape (batch_size, seq_len, hidden_size)
          masks: Tensor shape (batch_size, seq_len)
            Has 1s where there is real input, 0s where there's padding.

        Outputs:
          logits: Tensor shape (batch_size, seq_len)
            logits is the result of the downprojection layer, but it has -1e30
            (i.e. very large negative number) in the padded locations
          prob_dist: Tensor shape (batch_size, seq_len)
            The result of taking softmax over logits.
            This should have 0 in the padded locations, and the rest should sum to 1.
        """
        with vs.variable_scope("SimpleSoftmaxLayer"):

            # Linear downprojection layer
            logits = tf.contrib.layers.fully_connected(inputs, num_outputs=1, activation_fn=None) # shape (batch_size, seq_len, 1)
            logits = tf.squeeze(logits, axis=[2]) # shape (batch_size, seq_len)

            # Take softmax over sequence
            masked_logits, prob_dist = masked_softmax(logits, masks, 1)

            return masked_logits, prob_dist

class SelfAttn(object):
    """Module for self attention.
    """

    def __init__(self, keep_prob, hidden_size, attention_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          hidden_size: hidden size of the hidden representation. int
          attention_size: size of the attention vector
        """
        print("Building Self Attention Layer")
        self.keep_prob = keep_prob
        self.hidden_size = hidden_size
        self.attention_size = attention_size

    def build_graph(self, contexts, contexts_mask):
        """
        Inputs:
          contexts: Tensor shape (BS, N, H).
          contexts_mask: Tensor shape (BS, N).
            1s where there's real input, 0s where there's padding

        Outputs:
          output: Tensor shape (batch_size, N, H).
            This is the attention output
        """
        print("Graph for Self Attn")
        with vs.variable_scope("SelfAttn"):
            B = tf.shape(contexts)[0]
            N = tf.shape(contexts)[1]
            C = self.attention_size
            self.weights_1 = tf.get_variable(
                "W1",
                shape=(self.hidden_size, self.attention_size),
                initializer=tf.contrib.layers.xavier_initializer())
            self.weights_2 = tf.get_variable(
                "W2",
                shape=(self.hidden_size, self.attention_size),
                initializer=tf.contrib.layers.xavier_initializer())
            self.v = tf.get_variable(
                "v",
                shape=(self.attention_size, 1),
                initializer=tf.contrib.layers.xavier_initializer())

            values_1 = tf.reshape(tf.matmul(tf.reshape(contexts, (B * N, -1)), self.weights_1), (-1, N, C)) # BS x N x C
            values_2 = tf.reshape(tf.matmul(tf.reshape(contexts, (B * N, -1)), self.weights_2), (-1, N, C)) # BS x N x C
            tf.assert_equal(tf.shape(values_1), [B, N, C])
            tf.assert_equal(tf.shape(values_2), [B, N, C])
            values_1 = tf.expand_dims(values_1, axis=1) # BS x 1 x N x C
            values_2 = tf.expand_dims(values_2, axis=2) # BS x N x 1 x C
            additive_value = values_1 + values_2 # BS x N x N x C
            additive_value = tf.tanh(additive_value)
            tf.assert_equal(tf.shape(additive_value), [B, N, N, C])
            E = tf.matmul(tf.reshape(additive_value, (B * N * N, C)), self.v) # BS x N x N x 1
            E = tf.reshape(E, (-1, N, N)) # BS x N x N
            tf.assert_equal(tf.shape(E), [B, N, N])
            contexts_mask = tf.expand_dims(contexts_mask, 2) # BS x N x 1
            E_mask = contexts_mask * tf.transpose(contexts_mask, [0, 2, 1]) # BS x N x N
            _, attn_p = masked_softmax(E, E_mask, 2) # BS x N x N
            output = tf.matmul(attn_p, contexts) # BS x N x H
            tf.assert_equal(tf.shape(output), tf.shape(contexts))
            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)

            return attn_p, output

class BidirectionAttn(object):
    """Module for bidirectional Attention.
    """
    def __init__(self, keep_prob, hidden_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_size: size of the value vectors. int
        """
        self.keep_prob = keep_prob
        self.hidden_size = hidden_size
        print("Building BIDAF")
    def build_similarity_matrix(self, questions, contexts):
        """
        Inputs:
            questions: Hidden representations of questions (BS, M, 2H)
            contexts: Hidden representations of contexts (BS, N, 2H)
        Output:
            similarity: similarity matrix (BS, N, M) where
            S[_][i][j] = < w, [c_i, q_j, c_i o q_j]>
        """
        H = self.hidden_size
        BS = tf.shape(questions)[0]
        N = tf.shape(contexts)[1]
        M = tf.shape(questions)[1]

        w_sim_1 = tf.get_variable('w_sim_1', shape=(2*H),
            initializer=tf.contrib.layers.xavier_initializer()) # 2 * H
        w_sim_2 = tf.get_variable('w_sim_2', shape=(2*H),
            initializer=tf.contrib.layers.xavier_initializer()) # 2 * H
        w_sim_3 = tf.get_variable('w_sim_3', shape =(2*H),
            initializer=tf.contrib.layers.xavier_initializer()) # 2 * H

        # Compute matrix  of size BS x N x M x 2H which contains all c_i o q_j
        CW = tf.reshape(tf.reshape(contexts, (-1, 2 * H)) * tf.expand_dims(w_sim_3, 0), (-1, N, 2*H)) # BS x N x 2H
        #Compute all dot products
        term1 = tf.reshape(tf.matmul(tf.reshape(contexts, (BS * N, 2*H)), tf.expand_dims(w_sim_1, -1)), (-1, N)) # BS x N
        term2 = tf.reshape(tf.matmul(tf.reshape(questions, (BS * M, 2 * H)), tf.expand_dims(w_sim_2, -1)), (-1, M)) # BS x M
        term3 = tf.matmul(CW, tf.transpose(questions, [0, 2, 1])) # BS x N x M
        S = tf.reshape(term1,(-1, N, 1)) + term3 + tf.reshape(term2, (-1, 1, M))
        print("Building Similarity Matrix")
        return S

    def build_graph(self, questions, questions_mask, contexts, contexts_mask):
        """
        For each key, return an attention output vector for the values concatenated
        with a blended representation of the key vector.

        Inputs:
          questions: Tensor shape (batch_size, question_len, 2 * hidden_size).
          questions_mask: Tensor shape (batch_size, question_len).
            1s where there's real input, 0s where there's padding
          contexts: Tensor shape (batch_size, context_len, 2 * hidden_size)
          contexts_mask: Tensor shape (batch_size, context_len).
            1s where there's real input, 0s where there's padding

        Outputs:
          alpha : tensor shape (batch_size, context_len, question_len) attention distribution
          of context on questions
          beta : tensor shape (batch_size, context_len) attention distribution for context.
          values_output: Tensor shape (batch_size, context_len, 4 * hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights) concatenated with a
            weighted sum of the keys.
        """
        with vs.variable_scope("BidirectionAttn"):
            H = self.hidden_size
            BS = tf.shape(questions)[0]
            N = tf.shape(contexts)[1]
            M = tf.shape(questions)[1]
            S = self.build_similarity_matrix(questions, contexts) # (bacth_size, context_len, question_len)

            # Context to Question Attention
            # Build mask for similarity matrix
            questions_mask = tf.expand_dims(questions_mask, -1) # BS x M x 1
            contexts_mask = tf.expand_dims(contexts_mask, -1) # BS x N x 1
            S_mask = tf.matmul(
                contexts_mask,
                tf.transpose(questions_mask, (0, 2, 1))
            )

            S, alpha = masked_softmax(S, S_mask, 2) # (batch_size, context_len, question_len)
            tf.assert_equal(tf.shape(S), (BS, N, M))
            sum_alpha = tf.reduce_sum(alpha, axis=2) # batch_size, context_len
            tf.assert_equal(sum_alpha, tf.ones(shape=[BS, N]))
            c2q_output = tf.matmul(alpha, questions) # batch_size, context_len, 2*hidden_size)

            # Question to Context Attention
            m = tf.reduce_max(S, axis=2) # (batch_size, context_len)
            beta = tf.expand_dims(tf.nn.softmax(m), -1) # (batch_size, context_len, 1)
            beta = tf.transpose(beta, (0, 2, 1))
            q2c_output = tf.matmul(beta, contexts) # (batch_size, 1, 2 * h)

            q2c_output= tf.tile(q2c_output, (1, N, 1))
            output = tf.concat([c2q_output, c2q_output * contexts, q2c_output * contexts], axis=2) #batch_size, context_len, 6*hidden_size
            tf.assert_equal(tf.shape(output), [BS, N, 6*H])
            print("Bidirectional")
            # output = tf.Print(output, [output])
            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)
            return alpha, tf.reshape(beta, (-1, N)), output

class BasicAttn(object):
    """Module for basic attention.

    Note: in this module we use the terminology of "keys" and "values" (see lectures).
    In the terminology of "X attends to Y", "keys attend to values".

    In the baseline model, the keys are the context hidden states
    and the values are the question hidden states.

    We choose to use general terminology of keys and values in this module
    (rather than context and question) to avoid confusion if you reuse this
    module with other inputs.
    """

    def __init__(self, keep_prob, key_vec_size, value_vec_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_size: size of the value vectors. int
        """
        print("Building Basic Attention")
        self.keep_prob = keep_prob
        self.key_vec_size = key_vec_size
        self.value_vec_size = value_vec_size

    def build_graph(self, values, values_mask, keys):
        """
        Keys attend to values.
        For each key, return an attention distribution and an attention output vector.

        Inputs:
          values: Tensor shape (batch_size, num_values, value_vec_size).
          values_mask: Tensor shape (batch_size, num_values).
            1s where there's real input, 0s where there's padding
          keys: Tensor shape (batch_size, num_keys, value_vec_size)

        Outputs:
          attn_dist: Tensor shape (batch_size, num_keys, num_values).
            For each key, the distribution should sum to 1,
            and should be 0 in the value locations that correspond to padding.
          output: Tensor shape (batch_size, num_keys, hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights).
        """
        print("Graph for BasicAttn")
        with vs.variable_scope("BasicAttn"):

            # Calculate attention distribution
            values_t = tf.transpose(values, perm=[0, 2, 1]) # (batch_size, value_vec_size, num_values)
            attn_logits = tf.matmul(keys, values_t) # shape (batch_size, num_keys, num_values)
            attn_logits_mask = tf.expand_dims(values_mask, 1) # shape (batch_size, 1, num_values)
            _, attn_dist = masked_softmax(attn_logits, attn_logits_mask, 2) # shape (batch_size, num_keys, num_values). take softmax over values

            # Use attention distribution to take weighted sum of values
            output = tf.matmul(attn_dist, values) # shape (batch_size, num_keys, value_vec_size)

            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)

            return attn_dist, output


def masked_softmax(logits, mask, dim):
    """
    Takes masked softmax over given dimension of logits.

    Inputs:
      logits: Numpy array. We want to take softmax over dimension dim.
      mask: Numpy array of same shape as logits.
        Has 1s where there's real data in logits, 0 where there's padding
      dim: int. dimension over which to take softmax

    Returns:
      masked_logits: Numpy array same shape as logits.
        This is the same as logits, but with 1e30 subtracted
        (i.e. very large negative number) in the padding locations.
      prob_dist: Numpy array same shape as logits.
        The result of taking softmax over masked_logits in given dimension.
        Should be 0 in padding locations.
        Should sum to 1 over given dimension.
    """
    exp_mask = (1 - tf.cast(mask, 'float')) * (-1e30) # -large where there's padding, 0 elsewhere
    masked_logits = tf.add(logits, exp_mask) # where there's padding, set logits to -large
    prob_dist = tf.nn.softmax(masked_logits, dim)
    return masked_logits, prob_dist
