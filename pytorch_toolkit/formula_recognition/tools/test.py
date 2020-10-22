"""
 Copyright (c) 2020 Intel Corporation

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

import argparse
import json
import os.path
from functools import partial

import numpy as np
import onnxruntime
import torch
from im2latex.data.utils import collate_fn, create_list_of_transforms
from im2latex.data.vocab import END_TOKEN, START_TOKEN, read_vocab
from im2latex.datasets.im2latex_dataset import Im2LatexDataset
from im2latex.models.im2latex_model import Im2latexModel
from openvino.inference_engine import IECore
from torch.utils.data import DataLoader
from tqdm import tqdm
from tools.utils.common import (DECODER_INPUTS, DECODER_OUTPUTS,
                                ENCODER_INPUTS, ENCODER_OUTPUTS, read_net)
from tools.utils.evaluation_utils import Im2latexRenderBasedMetric
from tools.utils.get_config import get_config

spaces = [r'\,', r'\>', r'\;', r'\:', r'\quad', r'\qquad', '~']


def ends_with_space(string):
    """If string end with one of the latex spaces (given the above),
    returns True and index of this space, else False and None

    Args:
        string (str): input string with possible spaces

    Returns:
        Tuple(bool, int) string ends with space, index of the space
    """
    for idx, space in enumerate(spaces):
        if string.endswith(space):
            return True, idx
    return False, None


def get_onnx_inputs(model):
    names = []
    for inp in model.get_inputs():
        names.append(inp.name)
    return names


def get_onnx_outputs(model):
    names = []
    for out in model.get_outputs():
        names.append(out.name)
    return names


def postprocess_prediction(pred_phrase_str):
    """Deletes usual space in the end of the string and then checks
    if string ends with latex space. If yes, deletes latex space.
    Deletion of spaces is performed because, even though spaces in the end are invisible,
    they affect on rendering the formula, making it more tight to the left

    Args:
        pred_phrase_str (str): input string

    Returns:
        str: postprocessed string
    """
    pred_phrase_str = pred_phrase_str.rstrip()
    ends, idx = ends_with_space(pred_phrase_str)
    while ends:
        pred_phrase_str = pred_phrase_str[:len(pred_phrase_str) - len(spaces[idx])]
        pred_phrase_str = pred_phrase_str.rstrip()
        ends, idx = ends_with_space(pred_phrase_str)
    return pred_phrase_str


class ExportedModelEvaluator():
    def __init__(self, config):
        self.config = config
        self.load_ir_model()
        self.load_onnx_model()
        self.split = config.get("split", "validate")
        self.val_path = config.get("val_path")
        self.vocab = read_vocab(config.get('vocab_path'))
        self.load_dataset()
        self.read_expected_outputs()

    def load_ir_model(self):
        ie = IECore()
        encoder = read_net(self.config.get("res_encoder_name").replace(".onnx", ".xml"), ie)
        dec_step = read_net(self.config.get("res_decoder_name").replace(".onnx", ".xml"), ie)
        self.exec_net_encoder = ie.load_network(network=encoder, device_name="CPU")
        self.exec_net_decoder = ie.load_network(network=dec_step, device_name="CPU")

    def load_onnx_model(self):
        self.decoder_onnx = onnxruntime.InferenceSession(self.config.get("res_decoder_name"))
        self.encoder_onnx = onnxruntime.InferenceSession(self.config.get("res_encoder_name"))

    def get_onnx_metric(self):
        print("Loading onnx model")
        self.load_onnx_model()
        annotations = []
        predictions = []
        print("Starting onnx inference")
        metric = Im2latexRenderBasedMetric()
        for img_name, imgs, _, loss_computation_gt in tqdm(self.val_onnx_loader):
            imgs = imgs.clone().detach().numpy()
            row_enc_out, h, c, O_t = self.run_encoder(imgs)
            pred = self.run_decoder(h, c, O_t, row_enc_out).astype(np.int32)
            gold_phrase_str = self.vocab.construct_phrase(loss_computation_gt[0])
            pred_phrase_str = postprocess_prediction(self.vocab.construct_phrase(pred))
            annotations.append((gold_phrase_str, img_name[0]))
            predictions.append((pred_phrase_str, img_name[0]))
        res = metric.evaluate(annotations, predictions)
        return res

    def get_ir_metric(self):
        print("Loading OpenVINO IR model")
        self.load_ir_model()
        annotations = []
        predictions = []
        print("Starting OpenVINO IR inference")
        metric = Im2latexRenderBasedMetric()
        for img_name, imgs, _, loss_computation_gt in tqdm(self.val_ir_loader):
            imgs = imgs.clone().detach().numpy()
            targets_ir = self.run_ir_model(imgs)
            gold_phrase_str = self.vocab.construct_phrase(loss_computation_gt[0])
            pred_phrase_str = postprocess_prediction(self.vocab.construct_phrase(targets_ir))
            annotations.append((gold_phrase_str, img_name[0]))
            predictions.append((pred_phrase_str, img_name[0]))
        res = metric.evaluate(annotations, predictions)
        return res

    def run_ir_model(self, img):
        enc_res = self.exec_net_encoder.infer(inputs={self.config.get(
            "encoder_input_names", ENCODER_INPUTS).split(",")[0]: img})
        enc_out_names = self.config.get("encoder_output_names", ENCODER_OUTPUTS).split(",")
        ir_row_enc_out = enc_res[enc_out_names[0]]
        dec_states_h = enc_res[enc_out_names[1]]
        dec_states_c = enc_res[enc_out_names[2]]
        output = enc_res[enc_out_names[3]]
        dec_in_names = self.config.get("decoder_input_names", DECODER_INPUTS).split(",")
        dec_out_names = self.config.get("decoder_output_names", DECODER_OUTPUTS).split(",")
        tgt = np.array([[START_TOKEN]] * 1)
        logits = []
        for _ in range(256):
            dec_res = self.exec_net_decoder.infer(inputs={
                dec_in_names[0]: dec_states_h,
                dec_in_names[1]: dec_states_c,
                dec_in_names[2]: output,
                dec_in_names[3]: ir_row_enc_out,
                dec_in_names[4]: tgt
            }
            )

            dec_states_h = dec_res[dec_out_names[0]]
            dec_states_c = dec_res[dec_out_names[1]]
            output = dec_res[dec_out_names[2]]
            logit = dec_res[dec_out_names[3]]
            logits.append(logit)

            tgt = np.reshape(np.argmax(logit, axis=1), (1, 1)).astype(np.long)
            if tgt[0][0] == END_TOKEN:
                break
        return np.argmax(np.array(logits).squeeze(1), axis=1)

    def load_dataset(self):

        val_dataset = Im2LatexDataset(self.val_path, self.split)
        batch_transform_onnx = create_list_of_transforms(self.config.get('transforms_list'))
        self.val_onnx_loader = DataLoader(
            val_dataset,
            collate_fn=partial(collate_fn, self.vocab.sign2id,
                               batch_transform=batch_transform_onnx),
            num_workers=os.cpu_count())
        batch_transform_ir = create_list_of_transforms(self.config.get('transforms_list'), ovino_ir=True)
        self.val_ir_loader = DataLoader(
            val_dataset,
            collate_fn=partial(collate_fn, self.vocab.sign2id,
                               batch_transform=batch_transform_ir),
            num_workers=os.cpu_count())

    def read_expected_outputs(self):
        with open(self.config.get("expected_outputs")) as outputs_file:
            self.expected_outputs = json.load(outputs_file)

    def run_decoder(self, hidden, context, output, row_enc_out):

        decoder_inputs = get_onnx_inputs(self.decoder_onnx)
        decoder_outputs = get_onnx_outputs(self.decoder_onnx)
        logits = []
        logit = None
        for _ in range(256):
            if logit is not None:
                tgt = np.reshape(np.argmax(logit, axis=1), (1, 1)).astype(np.long)
            else:
                tgt = np.array([[START_TOKEN]] * 1)
            if tgt[0][0] == END_TOKEN:
                break
            hidden, context, output, logit = self.decoder_onnx.run(
                decoder_outputs,
                {
                    decoder_inputs[0]: hidden,
                    decoder_inputs[1]: context,
                    decoder_inputs[2]: output,
                    decoder_inputs[3]: row_enc_out,
                    decoder_inputs[4]: tgt
                })
            logits.append(logit)
        return np.argmax(np.array(logits).squeeze(1), axis=1)

    def run_encoder(self, img):
        encoder_outputs = get_onnx_outputs(self.encoder_onnx)
        encoder_input = get_onnx_inputs(self.encoder_onnx)[0]
        return self.encoder_onnx.run(encoder_outputs, {
            encoder_input: np.array(img, dtype=np.float32)
        })


class Evaluator:
    def __init__(self, config):
        self.config = config
        self.model_path = config.get('model_path')
        self.val_path = config.get('val_path')
        self.vocab = read_vocab(config.get('vocab_path'))
        self.val_transforms_list = config.get('val_transforms_list')
        self.split = config.get('split_file', 'validate')
        self.load_dataset()
        self.read_expected_outputs()
        self.model = Im2latexModel(config.get('backbone_type', 'resnet'), config.get(
            'backbone_config'), len(self.vocab), config.get('head', {}))
        self.device = config.get('device', 'cpu')
        if self.model_path is not None:
            self.model.load_weights(self.model_path, map_location=self.device)

        self.model = self.model.to(self.device)

    def load_dataset(self):
        val_dataset = Im2LatexDataset(self.val_path, self.split)
        batch_transform_val = create_list_of_transforms(self.val_transforms_list)
        self.val_loader = DataLoader(
            val_dataset,
            collate_fn=partial(collate_fn, self.vocab.sign2id,
                               batch_transform=batch_transform_val),
            num_workers=os.cpu_count())

    def read_expected_outputs(self):
        with open(self.config.get("expected_outputs")) as outputs_file:
            self.expected_outputs = json.load(outputs_file)

    def validate(self):
        self.model.eval()
        print("Validation started")
        annotations = []
        predictions = []
        metric = Im2latexRenderBasedMetric()
        with torch.no_grad():
            for img_name, imgs, training_gt, loss_computation_gt in tqdm(self.val_loader):
                imgs = imgs.to(self.device)
                training_gt = training_gt.to(self.device)
                loss_computation_gt = loss_computation_gt.to(self.device)
                _, pred = self.model(imgs)
                gold_phrase_str = self.vocab.construct_phrase(loss_computation_gt[0])
                pred_phrase_str = self.vocab.construct_phrase(pred[0])
                pred_phrase_str = postprocess_prediction(pred_phrase_str)
                annotations.append((gold_phrase_str, img_name[0]))
                predictions.append((pred_phrase_str, img_name[0]))
        res = metric.evaluate(annotations, predictions)
        return res


def parse_args():
    args = argparse.ArgumentParser()
    args.add_argument('--config')
    return args.parse_args()


if __name__ == "__main__":
    args = parse_args()
    test_config = get_config(args.config, section='eval')
    validator = Evaluator(test_config)
    result = validator.validate()
    print("Im2latex metric is: {}".format(result))
