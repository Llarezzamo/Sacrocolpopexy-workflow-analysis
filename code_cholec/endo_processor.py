#! /usr/bin/env python
import sys
from model import Endo3D
#from seq2seq_LSTM import seq2seq_LSTM
#from transformer.transformer import Transformer
from utils.sequence_loder import cholec_sequence_loder
import numpy as np
import visdom
from tqdm import tqdm

import time
import os
import scipy.io as scio
from scipy import stats
import random

import torch
import torchvision
import torchvision.transforms as transforms

import torch.nn as nn
from torch.utils import data
import pickle
import json

from sklearn.metrics import confusion_matrix


def phase_f1(seq_true, seq_test):
    seq_true = np.array(seq_true)
    seq_pred = np.array(seq_test)
    index = np.where(seq_true == 0)
    seq_true = np.delete(seq_true, index)
    seq_pred = np.delete(seq_pred, index)
    # f1 = f1_score(seq_true,seq_test,labels=[0, 1, 2, 3, 4, 5], average='weighted')
    # f1 = f1_score(seq_true, seq_test)

    phases = np.unique(seq_true)
    f1s = []
    for phase in phases:
        index_positive_in_true = np.where(seq_true == phase)
        index_positive_in_pred = np.where(seq_pred == phase)
        index_negative_in_true = np.where(seq_true != phase)
        index_negative_in_pred = np.where(seq_pred != phase)

        a = seq_true[index_positive_in_pred]
        unique, counts = np.unique(a, return_counts=True)
        count_dict = dict(zip(unique, counts))
        if phase in count_dict.keys():
            tp = count_dict[phase]
        else:
            tp = 0
        fp = len(index_positive_in_pred[0]) - tp

        b = seq_true[index_negative_in_pred]
        unique, counts = np.unique(b, return_counts=True)
        count_dict = dict(zip(unique, counts))
        if phase in count_dict.keys():
            fn = count_dict[phase]
        else:
            fn = 0
        tn = len(index_negative_in_pred[0]) - fn

        f1 = tp / (tp + 0.5 * (fp + fn))

        f1s.append(f1)

    return sum(f1s) / len(f1s)


def sequence_maker(model, video_name, device):
    cholec = cholec_sequence_loder(device)
    cholec.whole_len_output(video_name)
    whole_loder = data.DataLoader(cholec, 20)
    seq_pre = []
    seq_true = []
    fc_list = []
    for labels_val, inputs_val in whole_loder:
        inputs_val = inputs_val.float().to(device)
        labels_val = labels_val.long().to(device)
        with torch.no_grad():
            output, x_fc = model.forward_cov(inputs_val)
        _, predicted_labels = torch.max(output.cpu().data, 1)
        for i in range(predicted_labels.numpy().shape[0]):
            seq_pre.append(predicted_labels.numpy()[i])
            seq_true.append(labels_val.cpu().numpy()[i])
            fc_list.append(x_fc[i, :])
    return seq_pre, seq_true, fc_list



path = '/home/yitong/venv_yitong/cholec80_phase/data/whole'

current_path = os.path.abspath(os.getcwd())
videos_path = os.path.join(current_path, 'data/sacro_jpg')

video_list = ['video' + str(i).zfill(2) for i in range(1, 81)]

device = torch.device("cuda:1" if torch.cuda.is_available() else "cpu")
model = Endo3D().to(device)
model.load_state_dict(torch.load('./params/params_c3d_1200.pkl'))

f1 = 0
for video_name in video_list:
    save_path = os.path.join(path, video_name)

    os.mkdir(save_path)
    seq_pre, seq_true, fc_list = sequence_maker(model, video_name, device)

    a = open(os.path.join(save_path, 'seq_pred.pickle'), 'wb')
    pickle.dump(seq_pre, a)
    a.close()

    b = open(os.path.join(save_path, 'seq_true.pickle'), 'wb')
    pickle.dump(seq_true, b)
    b.close()

    c = open(os.path.join(save_path, 'fc_list.pickle'), 'wb')
    pickle.dump(fc_list, c)
    c.close()

#     f1 += phase_f1(seq_true, seq_pre)
#
# print(f1/20)





