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

"""This file contains the entrypoint to the rest of the code"""

from __future__ import absolute_import
from __future__ import division

import os
import io
import json
import sys
import logging

import tensorflow as tf
import numpy as np

from qa_model import QAModel
from qa_bidaf_model import QABidafModel
from qa_baseline_model import QABaselineModel
from qa_selfattn_model import QASelfAttnModel
from qa_stack_model import QAStackModel
from qa_pointer_model import QAPointerModel
from vocab import get_glove
from official_eval_helper import get_json_data, generate_answers, generate_distributions, generate_answers_from_dist


logging.basicConfig(level=logging.INFO)

MAIN_DIR = os.path.relpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) # relative path of the main directory
DEFAULT_DATA_DIR = os.path.join(MAIN_DIR, "data") # relative path of data dir
EXPERIMENTS_DIR = os.path.join(MAIN_DIR, "experiments") # relative path of experiments dir

# High-level options
tf.app.flags.DEFINE_integer("gpu", 0, "Which GPU to use, if you have multiple.")
tf.app.flags.DEFINE_string("mode", "train", "Available modes: train / show_examples / official_eval / getinfo")
tf.app.flags.DEFINE_string("experiment_name", "", "Unique name for your experiment. This will create a directory by this name in the experiments/ directory, which will hold all data related to this experiment")
tf.app.flags.DEFINE_integer("num_epochs", 0, "Number of epochs to train. 0 means train indefinitely")

# Model options
tf.app.flags.DEFINE_string("model_name", "bidaf", "Define the model to be used: baseline/bidaf/selfattn")
tf.app.flags.DEFINE_string("rnn_cell", "GRU", "Choose RNN cell GRU/LSTM")
tf.app.flags.DEFINE_integer("num_layers", 1, "Choose num of layers for embedding")
tf.app.flags.DEFINE_integer("selfattn_size", 100, "Choose size of self attention vectors.")
tf.app.flags.DEFINE_string("select_mode", "default", "Choose start/end position selection heuristic. default/endafter")

# Hyperparameters
tf.app.flags.DEFINE_float("learning_rate", 0.001, "Learning rate.")
tf.app.flags.DEFINE_float("max_gradient_norm", 5.0, "Clip gradients to this norm.")
tf.app.flags.DEFINE_float("dropout", 0.15, "Fraction of units randomly dropped on non-recurrent connections.")
tf.app.flags.DEFINE_integer("batch_size", 100, "Batch size to use")
tf.app.flags.DEFINE_integer("hidden_size", 200, "Size of the hidden states")
tf.app.flags.DEFINE_integer("context_len", 400, "The maximum context length of your model")
tf.app.flags.DEFINE_integer("question_len", 30, "The maximum question length of your model")
tf.app.flags.DEFINE_integer("embedding_size", 100, "Size of the pretrained word vectors. This needs to be one of the available GloVe dimensions: 50/100/200/300")

# How often to print, save, eval
tf.app.flags.DEFINE_integer("print_every", 1, "How many iterations to do per print.")
tf.app.flags.DEFINE_integer("save_every", 500, "How many iterations to do per save.")
tf.app.flags.DEFINE_integer("eval_every", 500, "How many iterations to do per calculating loss/f1/em on dev set. Warning: this is fairly time-consuming so don't do it too often.")
tf.app.flags.DEFINE_integer("keep", 1, "How many checkpoints to keep. 0 indicates keep all (you shouldn't need to do keep all though - it's very storage intensive).")

# Reading and saving data
tf.app.flags.DEFINE_string("train_dir", "", "Training directory to save the model parameters and other info. Defaults to experiments/{experiment_name}")
tf.app.flags.DEFINE_string("glove_path", "", "Path to glove .txt file. Defaults to data/glove.6B.{embedding_size}d.txt")
tf.app.flags.DEFINE_string("data_dir", DEFAULT_DATA_DIR, "Where to find preprocessed SQuAD data for training. Defaults to data/")
tf.app.flags.DEFINE_string("ckpt_load_dir", "", "For official_eval mode, which directory to load the checkpoint fron. You need to specify this for official_eval mode.")
tf.app.flags.DEFINE_string("json_in_path", "", "For official_eval mode, path to JSON input file. You need to specify this for official_eval_mode.")
tf.app.flags.DEFINE_string("json_out_path", "predictions.json", "Output path for official_eval mode. Defaults to predictions.json")
tf.app.flags.DEFINE_string("ensemble_dir", "", "Directory to put the ensemble outputs.")
tf.app.flags.DEFINE_string("ensemble_name", "", "Name of the output file containing the probability outputs.")

FLAGS = tf.app.flags.FLAGS
os.environ["CUDA_VISIBLE_DEVICES"] = str(FLAGS.gpu)


def initialize_model(session, model, train_dir, expect_exists):
    """
    Initializes model from train_dir.

    Inputs:
      session: TensorFlow session
      model: QAModel
      train_dir: path to directory where we'll look for checkpoint
      expect_exists: If True, throw an error if no checkpoint is found.
        If False, initialize fresh model if no checkpoint is found.
    """
    print "Looking for model at %s..." % train_dir
    ckpt = tf.train.get_checkpoint_state(train_dir)
    v2_path = ckpt.model_checkpoint_path + ".index" if ckpt else ""
    if ckpt and (tf.gfile.Exists(ckpt.model_checkpoint_path) or tf.gfile.Exists(v2_path)):
        print "Reading model parameters from %s" % ckpt.model_checkpoint_path
        model.saver.restore(session, ckpt.model_checkpoint_path)
    else:
        if expect_exists:
            raise Exception("There is no saved checkpoint at %s" % train_dir)
        else:
            print "There is no saved checkpoint at %s. Creating model with fresh parameters." % train_dir
            session.run(tf.global_variables_initializer())
            print 'Num params: %d' % sum(v.get_shape().num_elements() for v in tf.trainable_variables())


def main(unused_argv):
    # Print an error message if you've entered flags incorrectly
    if len(unused_argv) != 1:
        raise Exception("There is a problem with how you entered flags: %s" % unused_argv)

    # Check for Python 2
    if sys.version_info[0] != 2:
        raise Exception("ERROR: You must use Python 2 but you are running Python %i" % sys.version_info[0])

    # Print out Tensorflow version
    print "This code was developed and tested on TensorFlow 1.4.1. Your TensorFlow version: %s" % tf.__version__

    # Define train_dir
    if not FLAGS.experiment_name and not FLAGS.train_dir and \
            FLAGS.mode != "official_eval" and FLAGS.mode!= "ensemble_write" and FLAGS.mode!= "ensemble_predict":
        raise Exception("You need to specify either --experiment_name or --train_dir")
    FLAGS.train_dir = FLAGS.train_dir or os.path.join(EXPERIMENTS_DIR, FLAGS.experiment_name)

    # Initialize bestmodel directory
    bestmodel_dir = os.path.join(FLAGS.train_dir, "best_checkpoint")

    # Define path for glove vecs
    FLAGS.glove_path = FLAGS.glove_path or os.path.join(DEFAULT_DATA_DIR, "glove.6B.{}d.txt".format(FLAGS.embedding_size))

    # Load embedding matrix and vocab mappings
    emb_matrix, word2id, id2word = get_glove(FLAGS.glove_path, FLAGS.embedding_size)

    # Get filepaths to train/dev datafiles for tokenized queries, contexts and answers
    train_context_path = os.path.join(FLAGS.data_dir, "train.context")
    train_qn_path = os.path.join(FLAGS.data_dir, "train.question")
    train_ans_path = os.path.join(FLAGS.data_dir, "train.span")
    dev_context_path = os.path.join(FLAGS.data_dir, "dev.context")
    dev_qn_path = os.path.join(FLAGS.data_dir, "dev.question")
    dev_ans_path = os.path.join(FLAGS.data_dir, "dev.span")
    small_context_path = os.path.join(FLAGS.data_dir, "small.context")
    small_qn_path = os.path.join(FLAGS.data_dir, "small.question")
    small_ans_path = os.path.join(FLAGS.data_dir, "small.span")
    qa_model=None
    # Initialize model
    if FLAGS.model_name == "baseline":
        print("Using baseline model")
        qa_model = QABaselineModel(FLAGS, id2word, word2id, emb_matrix)
    elif FLAGS.model_name == "bidaf":
        qa_model = QABidafModel(FLAGS, id2word, word2id, emb_matrix)
    elif FLAGS.model_name == "selfattn":
        print("Using Self Attention")
        qa_model = QASelfAttnModel(FLAGS, id2word, word2id, emb_matrix)
    elif FLAGS.model_name == "stack":
        print("Using stack BIDAF/SA")
        qa_model = QAStackModel(FLAGS, id2word, word2id, emb_matrix)
    elif FLAGS.model_name == "pointer":
        print ("Using pointer model")
        qa_model= QAPointerModel(FLAGS, id2word, word2id, emb_matrix)

    # Some GPU settings
    config=tf.ConfigProto()
    config.gpu_options.allow_growth = True

    # Split by mode
    if FLAGS.mode == "train":
        # Setup train dir and logfile
        if not os.path.exists(FLAGS.train_dir):
            os.makedirs(FLAGS.train_dir)
        file_handler = logging.FileHandler(os.path.join(FLAGS.train_dir, "log.txt"))
        logging.getLogger().addHandler(file_handler)

        # Save a record of flags as a .json file in train_dir
        with open(os.path.join(FLAGS.train_dir, "flags.json"), 'w') as fout:
            json.dump(FLAGS.__flags, fout)

        # Make bestmodel dir if necessary
        if not os.path.exists(bestmodel_dir):
            os.makedirs(bestmodel_dir)

        with tf.Session(config=config) as sess:

            # Load most recent model
            initialize_model(sess, qa_model, FLAGS.train_dir, expect_exists=False)

            # Train
            qa_model.train(sess, train_context_path, train_qn_path, train_ans_path, dev_qn_path, dev_context_path, dev_ans_path)

    elif FLAGS.mode == "test":
        # Setup train dir and logfile
        if not os.path.exists(FLAGS.train_dir):
            os.makedirs(FLAGS.train_dir)
        file_handler = logging.FileHandler(os.path.join(FLAGS.train_dir, "log.txt"))
        logging.getLogger().addHandler(file_handler)

        # Save a record of flags as a .json file in train_dir
        with open(os.path.join(FLAGS.train_dir, "flags.json"), 'w') as fout:
            json.dump(FLAGS.__flags, fout)

        # Make bestmodel dir if necessary
        if not os.path.exists(bestmodel_dir):
            os.makedirs(bestmodel_dir)

        with tf.Session(config=config) as sess:

            # Load most recent model
            initialize_model(sess, qa_model, FLAGS.train_dir, expect_exists=False)

            # Train
            qa_model.train(sess, small_context_path, small_qn_path, small_ans_path, dev_qn_path, dev_context_path, dev_ans_path)

    elif FLAGS.mode == "show_examples":
        with tf.Session(config=config) as sess:

            # Load best model
            initialize_model(sess, qa_model, bestmodel_dir, expect_exists=True)

            # Show examples with F1/EM scores
            _, _ = qa_model.check_f1_em(sess, dev_context_path, dev_qn_path, dev_ans_path, "dev", num_samples=10, print_to_screen=True)

    elif FLAGS.mode == "visualize":
        with tf.Session(config=config) as sess:

            # Load best model
            initialize_model(sess, qa_model, bestmodel_dir, expect_exists=True)
            # Get distribution of begin and end spans.
            begin_total, end_total, f1_em_scores = qa_model.get_spans(sess, dev_context_path, dev_qn_path, dev_ans_path, "dev")
            np.save(os.path.join(FLAGS.train_dir, "begin_span"), begin_total)
            np.save(os.path.join(FLAGS.train_dir, "end_span"), end_total)
            np.save(os.path.join(FLAGS.train_dir, "f1_em"), f1_em_scores)

            # Visualize distribution of Context to Question attention
            c2q_attn = qa_model.get_c2q_attention(sess, dev_context_path, dev_qn_path, dev_ans_path, "dev", num_samples=0)
            np.save(os.path.join(FLAGS.train_dir, "c2q_attn"), c2q_attn)
            q2c_attn = qa_model.get_q2c_attention(sess, dev_context_path, dev_qn_path, dev_ans_path, "dev", num_samples=0)
            if len(q2c_attn > 0):
                np.save(os.path.join(FLAGS.train_dir, "q2c_attn"), q2c_attn)
            else:
                print 'This model doesn\'t have question to context attention'
            self_attn = qa_model.get_self_attention(sess, dev_context_path, dev_qn_path, dev_ans_path, "dev", num_samples=20)
            if len(self_attn > 0):
                 np.save(os.path.join(FLAGS.train_dir, "self_attn"), self_attn)
            else:
                print 'This model doesn\'t have self attention'


    elif FLAGS.mode == "ensemble_write":
        if FLAGS.json_in_path == "":
            raise Exception("For ensembling mode, you need to specify --json_in_path")
        if FLAGS.ckpt_load_dir == "":
            raise Exception("For ensembling mode, you need to specify --ckpt_load_dir")
        if FLAGS.ensemble_name == "":
            raise Exception("For ensembling mode, you need to specify --ensemble_name")
        # Read the JSON data from file
        qn_uuid_data, context_token_data, qn_token_data = get_json_data(FLAGS.json_in_path)

        with tf.Session(config=config) as sess:
            # Load model
            initialize_model(sess, qa_model, FLAGS.ckpt_load_dir, expect_exists=True)

            distributions = generate_distributions(sess, qa_model, word2id, qn_uuid_data, context_token_data, qn_token_data)
            # np uuid -> [start_dist, end_dist]
            # Write the uuid->answer mapping a to json file in root dir
            save_path= os.path.join(FLAGS.ensemble_dir, "distribution_" + FLAGS.ensemble_name+ '.json')
            print "Writing distributions to %s..." % save_path
            with io.open(save_path, 'w', encoding='utf-8') as f:
                f.write(unicode(json.dumps(distributions, ensure_ascii=False)))
                print "Wrote distributions to %s" % save_path

    elif FLAGS.mode == "ensemble_predict":
        if FLAGS.json_in_path == "":
            raise Exception("For ensembling mode, you need to specify --json_in_path")
        models = ['stack', 'pointer']
        distributions = [os.path.join(FLAGS.ensemble_dir, "distribution_" + m + ".json") for m in models]
        total_dict = {}
        for d in distributions:
            with open(d) as prediction_file:
                print d
                predictions = json.load(prediction_file)
                for (key, item) in predictions.items():
                    if total_dict.get(key, None) is None:
                        total_dict[key] = np.asarray(item)
                    else:
                        total_dict[key] += np.asarray(item)

        for (key, item) in total_dict.items():
            total_dict[key][0]/=len(models)
            total_dict[key][1] /=len(models)
        # Read the JSON data from file
        qn_uuid_data, context_token_data, qn_token_data = get_json_data(FLAGS.json_in_path)
        answers_dict = generate_answers_from_dist(None, qa_model, total_dict, word2id, qn_uuid_data, context_token_data, qn_token_data)

        # Write the uuid->answer mapping a to json file in root dir
        print "Writing predictions to %s..." % FLAGS.json_out_path
        with io.open(FLAGS.json_out_path, 'w', encoding='utf-8') as f:
            f.write(unicode(json.dumps(answers_dict, ensure_ascii=False)))
            print "Wrote predictions to %s" % FLAGS.json_out_path

    elif FLAGS.mode == "official_eval":
        if FLAGS.json_in_path == "":
            raise Exception("For official_eval mode, you need to specify --json_in_path")
        if FLAGS.ckpt_load_dir == "":
            raise Exception("For official_eval mode, you need to specify --ckpt_load_dir")

        # Read the JSON data from file
        qn_uuid_data, context_token_data, qn_token_data = get_json_data(FLAGS.json_in_path)

        with tf.Session(config=config) as sess:

            # Load model from ckpt_load_dir
            initialize_model(sess, qa_model, FLAGS.ckpt_load_dir, expect_exists=True)

            # Get a predicted answer for each example in the data
            # Return a mapping answers_dict from uuid to answer
            answers_dict = generate_answers(sess, qa_model, word2id, qn_uuid_data, context_token_data, qn_token_data)

            # Write the uuid->answer mapping a to json file in root dir
            print "Writing predictions to %s..." % FLAGS.json_out_path
            with io.open(FLAGS.json_out_path, 'w', encoding='utf-8') as f:
                f.write(unicode(json.dumps(answers_dict, ensure_ascii=False)))
                print "Wrote predictions to %s" % FLAGS.json_out_path


    else:
        raise Exception("Unexpected value of FLAGS.mode: %s" % FLAGS.mode)

if __name__ == "__main__":
    tf.app.run()
