import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.distributions as D
import torchvision.transforms as T
from torch.utils.data import DataLoader
from torch.utils.checkpoint import checkpoint
from tensorboardX import SummaryWriter

import numpy as np

from modules import WaveletNet

import os
import time
import math
import argparse
import pprint
import pdb
pdb.set_trace()

parser = argparse.ArgumentParser()
# action
parser.add_argument('--train', action='store_true', help='Train a flow.')
parser.add_argument('--restore_file', type=str, help='Path to model to restore.')
parser.add_argument('--seed', type=int, help='Random seed to use.')
# paths and reporting
parser.add_argument('--output_dir', default='./results/{}'.format(os.path.splitext(__file__)[0]))
parser.add_argument('--results_file', default='results.txt', help='Filename where to store settings and test results.')
parser.add_argument('--log_interval', type=int, default=100, help='How often to show loss statistics and save samples.')
parser.add_argument('--save_interval', type=int, default=200, help='How often to save during training.')
parser.add_argument('--eval_interval', type=int, default=1, help='Number of epochs to eval model and save model checkpoint.')
# training params
parser.add_argument('--batch_size', type=int, default=16, help='Training batch size.')
parser.add_argument('--n_epochs', type=int, default=10, help='Number of epochs to train.')
parser.add_argument('--start_epoch', default=0, help='Starting epoch (for logging; to be overwritten when restoring file.')
parser.add_argument('--lr', type=float, default=1e-3, help='Learning rate.')
parser.add_argument('--grad_norm_clip', default=50, type=float, help='Clip gradients during training.')

# --------------------
# Data
# --------------------
def fetch_dataloader(args):
    dimensionality = 64
    n_samples = args.batch_size
    loc = torch.rand(dimensionality).double()
    scale_tril = torch.tensor(np.tril(np.random.rand(dimensionality, dimensionality)+.1)).double()
    dist = D.MultivariateNormal(loc=loc, scale_tril=scale_tril)
    x_sample = dist.sample((n_samples,)).float()
    data_loader = DataLoader(dataset=x_sample, batch_size=args.batch_size)
    return data_loader

# --------------------
# Train and evaluate
# --------------------
def train_epoch(model, dataloader, optimizer, writer, epoch, args):
    model.train()

    tic = time.time()
    for i, x in enumerate(dataloader):
        args.step += 1
        loss = - model.log_prob(x).mean()
        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), args.grad_norm_clip)
        optimizer.step()
        # report stats
        if i % args.log_interval == 0:
            # compute KL divergence between base and each of the z's that the model produces
            with torch.no_grad():
                zs, _ = model(x)
                kls = [D.kl.kl_divergence(D.Normal(z.mean(), z.std()), model.base_dist) for z in zs]
            # write stats
            et = time.time() - tic              # elapsed time
            tt = len(dataloader) * et / (i+1)   # total time per epoch
            print('Epoch: [{}/{}][{}/{}]\tStep: {}\tTime: elapsed {:.0f}m{:02.0f}s / total {:.0f}m{:02.0f}s\tLoss {:.4f}\t'.format(
                    epoch, args.start_epoch + args.n_epochs, i+1, len(dataloader), args.step, et//60, et%60, tt//60, tt%60, loss.item()))
            # update writer
            for j, kl in enumerate(kls):
                writer.add_scalar('kl_level_{}'.format(j), kl.item(), args.step)

        if i % args.save_interval == 0:
            # save training checkpoint
            torch.save({'epoch': epoch,
                        'global_step': args.step,
                        'state_dict': model.state_dict()},
                        os.path.join(args.output_dir, 'checkpoint.pt'))
            torch.save(optimizer.state_dict(), os.path.join(args.output_dir, 'optim_checkpoint.pt'))

def train(model, train_dataloader, optimizer, writer, args):
    for epoch in range(args.start_epoch, args.start_epoch + args.n_epochs):
        train_epoch(model, train_dataloader, optimizer, writer, epoch, args)

# --------------------
# Main
# --------------------
if __name__ == '__main__':
    args = parser.parse_args()
    args.step = 0  # global step
    args.output_dir = os.path.dirname(args.restore_file) if args.restore_file else os.path.join(args.output_dir, time.strftime('%Y-%m-%d_%H-%M-%S', time.gmtime()))
    writer = None  # init as None in case of multiprocessing; only main process performs write ops

    # setup seed
    if args.seed:
        torch.manual_seed(args.seed)

    # load data; sets args.input_dims needed for setting up the model
    train_dataloader = fetch_dataloader(args)

    # load model
    model = WaveletNet()
    for p in model.parameters():
        print(p)

    # load optimizers
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    # load checkpoint if provided
    if args.restore_file:
        model_checkpoint = torch.load(args.restore_file)
        model.load_state_dict(model_checkpoint['state_dict'])
        optimizer.load_state_dict(torch.load(os.path.dirname(args.restore_file) + '/optim_checkpoint.pt'))
        args.start_epoch = model_checkpoint['epoch']
        args.step = model_checkpoint['global_step']

    # setup writer and outputs
    writer = SummaryWriter(log_dir = args.output_dir)

    # save settings
    config = 'Parsed args:\n{}\n\n'.format(pprint.pformat(args.__dict__)) + \
                'Num trainable params: {:,.0f}\n\n'.format(sum(p.numel() for p in model.parameters())) + \
                'Model:\n{}'.format(model)
    config_path = os.path.join(args.output_dir, 'config.txt')
    writer.add_text('model_config', config)
    if not os.path.exists(config_path):
        with open(config_path, 'a') as f:
            print(config, file=f)

    if args.train:
        train(model, train_dataloader, optimizer, writer, args)

    writer.close()