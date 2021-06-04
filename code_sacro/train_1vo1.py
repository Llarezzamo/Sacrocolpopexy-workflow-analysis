#! /usr/bin/env python
from model import Endo3D_1vo, Endo3D_1vo1, Endo3D_1vo2
from utils.sacro_loder import sacro_loder
import numpy as np

import pickle
import time
import psutil
import os
import scipy.io as scio
from tqdm import tqdm
import visdom

import torch
import torchvision
import torchvision.transforms as transforms

import torch.nn as nn
import torch.autograd as autograd
import torch.optim as optim
from torch.utils import data

from sklearn.metrics import confusion_matrix


def focal_loss(x, y):
    '''Focal loss.
    Args:
      x: (tensor) sized [N,D].
      y: (tensor) sized [N,].
    Return:
      (tensor) focal loss.
    '''
    alpha = 0.25
    gamma = 2

    #t = one_hot_embedding(y.data.cpu(), 1+self.num_classes)  # [N,21]
    t = y.data.reshape((-1, 1))
    #t = t[:,1:]  # exclude background
    #t = Variable(t)  # [N,20]

    p = x.sigmoid()
    pt = p*t + (1-p)*(1-t)         # pt = p if t > 0 else 1-p
    w = (1-alpha)*t + alpha*(1-t)  # w = alpha if t > 0 else 1-alpha
    w = w * (1-pt).pow(gamma)
    return torch.nn.functional.binary_cross_entropy_with_logits(x, t, w.data, reduction='sum')


def main():
    vis = visdom.Visdom(env='Endo3D')
    device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")
    model = Endo3D_1vo1().to(device)

    # load pre-trained parameters
    Endo3D_state_dict = model.state_dict()
    # pre_state_dict = torch.load('./params/params_conv3.pkl')
    pre_state_dict = torch.load('./params/params_endo3d_save_point.pkl')
    new_state_dict = {k: v for k, v in pre_state_dict.items() if k in Endo3D_state_dict}
    Endo3D_state_dict.update(new_state_dict)
    model.load_state_dict(Endo3D_state_dict)
    # model.load_state_dict(torch.load('./params/params_endo3d_1vo2.pkl'))

    # loading the training and validation set
    print('loading data')
    start = time.time()
    evaluation_slot = 25
    rebuild_slot = 7
    sacro = sacro_loder(train_epoch_size=5000, validation_epoch_size=600, cv_div='dataset_div4.json')
    train_loader = data.DataLoader(sacro, 50, shuffle=True)
    valid_loader = data.DataLoader(sacro, 60, shuffle=True)
    elapsed = (time.time() - start)
    print("Data loded, time used:", elapsed)

    # Initializing necessary components
    w = torch.tensor([0.2, 1, 1, 1, 1, 1])
    loss_func = nn.NLLLoss(weight=w).to(device)
    # optimizer = optim.Adam(model.parameters(), lr=1e-5, weight_decay=3e-5)
    optimizer = optim.SGD(filter(lambda p: p.requires_grad, model.parameters()), lr=3e-4, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=1)

    accuracy_stas = []
    loss_stas = []
    counter = 0
    best_accuracy = 0

    print('Start training')
    start = time.time()
    for epoch in range(100):
        # reconstruct the train set for every 7 epochs
        if epoch % rebuild_slot == 0 and epoch != 0:
            sacro.build_epoch()
            # sacro.build_validation()

        print('epoch:', epoch + 1)
        sacro.mode = 'train'
        for labels, inputs in tqdm(train_loader, ncols=80):
            counter += 1
            model.train()
            inputs = inputs.float().to(device)
            labels = labels.long().to(device)

            optimizer.zero_grad()
            output = model.forward_cov(inputs)
            train_loss = loss_func(output, labels)
            train_loss.backward()
            # torch.nn.utils.clip_grad_norm(model.parameters(), 10)
            optimizer.step()

            if counter % evaluation_slot == 0:
                # Evaluation on training set
                _, predicted_labels = torch.max(output.cpu().data, 1)
                correct_pred = (predicted_labels == labels.cpu()).sum().item()
                total_pred = predicted_labels.size(0)
                train_accuracy = correct_pred / total_pred * 100

                # visualization in visdom
                y_train_acc = torch.Tensor([train_accuracy])
                y_train_loss = torch.Tensor([train_loss.item()])


                # Evaluation on validation set
                model.eval()
                sacro.mode = 'validation'
                running_loss = 0.0
                running_accuracy = 0.0
                valid_num = 0
                cm = np.zeros((6, 6))
                for labels_val, inputs_val in valid_loader:
                    inputs_val = inputs_val.float().to(device)
                    labels_val = labels_val.long().to(device)
                    output = model.forward_cov(inputs_val)
                    valid_loss = loss_func(output, labels_val)

                    # Calculate the loss with new parameters
                    running_loss += valid_loss.item()
                    # current_loss = running_loss / (batch_counter + 1)

                    _, predicted_labels = torch.max(output.cpu().data, 1)
                    cm += confusion_matrix(labels_val.cpu().numpy(), predicted_labels, labels=[0, 1, 2, 3, 4, 5])
                    correct_pred = (predicted_labels == labels_val.cpu()).sum().item()
                    total_pred = predicted_labels.size(0)
                    accuracy = correct_pred / total_pred
                    running_accuracy += accuracy
                    valid_num += 1
                    # current_accuracy = running_accuracy / (batch_counter + 1) * 100
                batch_loss = running_loss / valid_num
                batch_accuracy = running_accuracy / valid_num * 100
                sacro.mode = 'train'

                # save the loss and accuracy
                accuracy_stas.append(batch_accuracy)
                loss_stas.append(batch_loss)

                # visualization in visdom
                x = torch.Tensor([counter])
                y_batch_acc = torch.Tensor([batch_accuracy])
                y_batch_loss = torch.Tensor([batch_loss])
                txt1 = ''.join(['t%d:%d ' % (i, np.sum(cm, axis=1)[i]) for i in range(len(np.sum(cm, axis=1)))])
                txt2 = ''.join(['p%d:%d ' % (i, np.sum(cm, axis=0)[i]) for i in range(len(np.sum(cm, axis=0)))])
                vis.text((txt1 + '<br>' + txt2), win='summary', opts=dict(title='Summary'))
                cm = cm / np.sum(cm, axis=1)
                vis.heatmap(X=cm, win='heatmap', opts=dict(title='confusion matrix',
                                                           rownames=['t0', 't1', 't2', 't3', 't4', 't5'],
                                                           columnnames=['p0', 'p1', 'p2', 'p3', 'p4', 'p5']))
                vis.line(X=x, Y=np.column_stack((y_train_acc, y_batch_acc)), win='accuracy', update='append',
                         opts=dict(title='accuracy', showlegend=True, legend=['train', 'valid']))
                vis.line(X=x, Y=np.column_stack((y_train_loss, y_batch_loss)), win='loss', update='append',
                         opts=dict(title='loss', showlegend=True, legend=['train', 'valid']))

                # Save point
                if batch_accuracy > best_accuracy:
                    best_accuracy = batch_accuracy
                    torch.save(model.state_dict(), './params/params_endo3d_1vo1_save_point.pkl')
                    print('the current best accuracy is: %.3f %%' % best_accuracy)

                # viz.image(img, opts=dict(title='Example input'))
        print('[Final results of epoch: %d] train loss: %.3f train accuracy: %.3f %%'
              ' validation loss: %.3f validation accuracy: %.3f %%'
              % (epoch + 1, train_loss, train_accuracy, batch_loss, batch_accuracy))
        scheduler.step()
    elapsed = (time.time() - start)
    print("Training finished, time used:", elapsed / 60, 'min')

    # torch.save(model.state_dict(), 'params_endo3d.pkl')

    data_path = 'results_output.mat'
    scio.savemat(data_path, {'accuracy': np.asarray(accuracy_stas), 'loss': np.asarray(loss_stas)})


if __name__ == '__main__':
    main()
