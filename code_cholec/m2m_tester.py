from many2many_LSTM import many2many_LSTM
from transformer.transformer import Transformer
import numpy as np

import pickle
import time
import psutil
import os
import scipy.io as scio
from tqdm import tqdm
import visdom
import random
import json

import torch
import torchvision
import torchvision.transforms as transforms
import torch.nn.functional as F

import torch.nn as nn
import torch.autograd as autograd
import torch.optim as optim
from torch.utils import data
import time
from sklearn.metrics import confusion_matrix


class data_loder(data.Dataset):
    def __init__(self, video_name):
        open_path = os.path.join('/home/yitong/venv_yitong/cholec80_phase/data/whole', video_name)

        file = open(os.path.join(open_path, 'seq_pred.pickle'), 'rb')
        self.seq_pre = pickle.load(file)
        file.close()

        file = open(os.path.join(open_path, 'seq_true.pickle'), 'rb')
        self.seq_true = pickle.load(file)
        file.close()

        file = open(os.path.join(open_path, 'fc_list.pickle'), 'rb')
        self.fc_list = pickle.load(file)
        file.close()

        self.seq_len = len(self.seq_true)
        current_path = os.path.abspath(os.getcwd())

        # self.fcs = torch.cat((torch.stack(self.fc_list).cpu(), torch.zeros(1000 - self.seq_len, 1200)))
        self.fcs = torch.stack(self.fc_list).cpu()

    def __getitem__(self, idx):
        M = torch.ones(1000, 1200)
        M[idx + 1:, :] = 0
        sequence_input_fc = self.fcs * M
        return {'fc': sequence_input_fc.unsqueeze(0),
                'true': torch.tensor(self.seq_true[0:idx + 1]).unsqueeze(0),
                'pred': torch.tensor(self.seq_pre[0: idx + 1]).unsqueeze(0)}

    def __len__(self):
        return self.seq_len


def sequence_loss(output, labels, device):
    # alpha = np.array([28.33, 11.23, 5.00, 2.09, 36.36, 11.01, 5.98]) / 100
    # loss_func = FocalLoss(7, alpha=alpha).to(device)
    # w = [0.1, 1.6, 0.5, 0.8, 2.0, 1, 1]
    # w = torch.tensor([0.1, 1.5, 0.3, 0.6, 2.0, 1, 1])
    w = torch.tensor([1.0, 1, 1, 1, 1, 1, 1])
    loss_func = nn.NLLLoss(weight=w).to(device)
    l = output.size()[1]
    pred_labels = [torch.max(output[:, i, :].data, 1)[1] for i in range(l - 1)]
    pred_labels.insert(0, torch.max(output[:, 0, :].data, 1)[1])
    loss_cont = sum([loss_func(output[:, i, :], pred_labels[i]) for i in range(l)]) / l
    loss = sum([loss_func(output[:, i, :], labels[:, i]) for i in range(l)]) / l
    return loss + 0 * loss_cont


def random_replace(trg_seq, portion_replaced):
    size = int(trg_seq.size(0) * trg_seq.size(1) * portion_replaced)
    a = []
    b = []
    for i in range(trg_seq.size(0)):
        for j in range(trg_seq.size(1)):
            a.append(i)
            b.append(j)
    while len(a) > size:
        idx = random.choice(range(len(a)))
        del (a[idx])
        del (b[idx])
    trg_seq[a, b] = torch.from_numpy(np.random.randint(7, size=size)).long()
    return trg_seq


def phase_f1(seq_true, seq_test):
    seq_true = np.array(seq_true)
    seq_pred = np.array(seq_test)
    index = np.where(seq_true == 0)
    seq_true = np.delete(seq_true, index)
    seq_pred = np.delete(seq_pred, index)
    # f1 = f1_score(seq_true,seq_test,labels=[0, 1, 2, 3, 4, 5], average='weighted')
    # f1 = f1_score(seq_true, seq_test)

    correct_pred_eval = (torch.tensor(seq_pred) == torch.tensor(seq_true)).sum().item()
    total_pred_eval = torch.tensor(seq_true).size(0)
    acc = correct_pred_eval / total_pred_eval

    phases, weight = np.unique(seq_true, return_counts=True)
    weight = weight/sum(weight)
    weight = dict(zip(phases, weight))
    f1s = {}
    precisions = {}
    recalls = {}
    f1_micro = 0
    for phase in range(1, 6):
        if phase in phases:
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
            if tp != 0:
                precision = tp / (tp + fp)
                recall = tp / (tp + fn)
            else:
                precision = 0
                recall = 0

            f1_micro += f1 * weight[phase]
            f1s.update({phase: f1})
            precisions.update({phase: precision})
            recalls.update({phase: recall})
        else:
            f1s.update({phase: np.NaN})
            precisions.update({phase: np.NaN})
            recalls.update({phase: np.NaN})
    return {'f1': f1s, 'precision': precisions, 'recall': recalls,
            'f1_avg': sum(f1s) / len(f1s),
            'f1_micro': f1_micro, 'acc_micro': acc,
            'precision_avg': sum(precisions) / len(precisions),
            'recall_avg': sum(recalls) / len(recalls)}


def evlauation(video_list, model, device, vis):
    total_video_num = len(video_list)
    running_loss = 0
    running_accuracy = 0
    acc_micro = []
    detail_results_list = []
    cm = np.zeros((7, 7))
    for video_name in tqdm(video_list, ncols=80):
        model.eval()
        vis.close('Surgical workflow sequential')
        video_loaded = data_loder(video_name)
        inputs = video_loaded.fcs.unsqueeze(0).to(device)
        labels = torch.tensor(video_loaded.seq_true).long().unsqueeze(0).to(device)
        with torch.no_grad():
            output = model.forward(inputs)
        _, predicted_labels = torch.max(output.cpu().data, 2)
        loss = sequence_loss(output, labels, device)
        predictions = predicted_labels[:, 0:len(video_loaded)]
        cm += confusion_matrix(labels.cpu().numpy().reshape(-1, ), predictions.view(-1, ), labels=[0, 1, 2, 3, 4, 5, 6])
        correct_pred = (predictions == labels.cpu()).sum().item()
        total_pred = predicted_labels.size(0) * predicted_labels.size(1)
        accuracy = correct_pred / total_pred * 100
        running_loss += loss.item()
        running_accuracy += accuracy
        results = phase_f1(labels.cpu().numpy().reshape(-1, ), (predictions.view(-1, )))
        acc_micro.append(results['acc_micro'])
        detail_results_list.append(results)
        vis.line(X=np.array(range(len(labels.cpu().numpy().reshape(-1, )))),
                 Y=np.column_stack((predictions.view(-1, ), labels.cpu().numpy().reshape(-1, ))),
                 win='Surgical workflow sequential', update='append',
                 opts=dict(title='Surgical workflow sequential', showlegend=True,
                           legend=['Prediction', 'Ground Truth']))
    running_loss = running_loss / total_video_num
    running_accuracy = running_accuracy / total_video_num
    cm = cm / np.sum(cm, axis=1)[:, None]
    return running_loss, running_accuracy, cm, detail_results_list, acc_micro


test_video_list = ['video' + str(i).zfill(2) for i in range(61, 81)]

vis = visdom.Visdom(env='sequence_tester')
device = torch.device("cuda:1" if torch.cuda.is_available() else "cpu")
model = many2many_LSTM(hidden_dim=1200, num_layers=3).to(device)
model.load_state_dict(torch.load('./params/params_LSTM_m2m_b10_ma.pkl'))
# model.load_state_dict(torch.load('./params/params_endo3d.pkl'))

y_batch_loss,  y_batch_acc, cm, detail_results_list, acc_micro = evlauation(test_video_list, model, device, vis)
detail_precision = np.zeros((len(detail_results_list), 5))
detail_recall = np.zeros((len(detail_results_list), 5))
accuracy_std = np.std(np.array(acc_micro))
print('accuracy:', sum(acc_micro) / len(acc_micro), accuracy_std)
for idx, item in enumerate(detail_results_list):
    detail_precision[idx, :] = np.array(np.array(list(item['precision'].values())))
    detail_recall[idx, :] = np.array(np.array(list(item['recall'].values())))
precision_std = np.std(np.nanmean(detail_precision, axis=1))
recall_std = np.std(np.nanmean(detail_recall, axis=1))
print('precisions:', np.nanmean(detail_precision), precision_std)
print('recalls:', np.nanmean(detail_recall), recall_std)
print(np.mean(np.diag(cm)))

vis.heatmap(X=cm, win='heatmap', opts=dict(title='confusion matrix',
                                           rownames=['t0', 't1', 't2', 't3', 't4', 't5', 't_not'],
                                           columnnames=['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p_not']))

