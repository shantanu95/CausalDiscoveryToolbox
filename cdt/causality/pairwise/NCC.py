u"""Neural Causation Coefficient.

Author : David Lopez-Paz
Ref :  Lopez-Paz, D. and Nishihara, R. and Chintala, S. and Schölkopf, B. and Bottou, L.,
    "Discovering Causal Signals in Images", CVPR 2017.

.. MIT License
..
.. Copyright (c) 2018 Diviyan Kalainathan
..
.. Permission is hereby granted, free of charge, to any person obtaining a copy
.. of this software and associated documentation files (the "Software"), to deal
.. in the Software without restriction, including without limitation the rights
.. to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
.. copies of the Software, and to permit persons to whom the Software is
.. furnished to do so, subject to the following conditions:
..
.. The above copyright notice and this permission notice shall be included in all
.. copies or substantial portions of the Software.
..
.. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
.. IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
.. FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
.. AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
.. LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
.. OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
.. SOFTWARE.
"""

from sklearn.preprocessing import scale
import numpy as np
import torch as th
import pandas as pd
from torch.autograd import Variable
from .model import PairwiseModel
from tqdm import trange
from torch.utils import data
from ...utils.Settings import SETTINGS


class Dataset(data.Dataset):
  'Characterizes a dataset for PyTorch'
  def __init__(self, dataset, labels):
        'Initialization'
        self.labels = labels
        self.dataset = dataset

  def __len__(self):
        'Denotes the total number of samples'
        return len(self.dataset)

  def __getitem__(self, index):
        'Generates one sample of data'
        # Select sample

        # Load data and get label

        return self.dataset[index], self.labels[index]


class NCC_model(th.nn.Module):
    """NCC model structure."""

    def __init__(self, n_hiddens=20, kernel_size=3):
        """Init the NCC structure with the number of hidden units.

        :param n_hiddens: Number of hidden units
        :type n_hiddens: int
        """
        super(NCC_model, self).__init__()
        self.conv = th.nn.Sequential(th.nn.Conv1d(2, n_hiddens, kernel_size),
                                     th.nn.ReLU(),
                                     th.nn.Conv1d(n_hiddens, n_hiddens,
                                                  kernel_size),
                                     th.nn.ReLU())
        # self.batch_norm = th.nn.BatchNorm1d(n_hiddens, affine=False)
        self.dense = th.nn.Sequential(th.nn.Linear(n_hiddens, n_hiddens),
                                      th.nn.ReLU(),
                                      th.nn.Linear(n_hiddens, 1)
                                      )

    def forward(self, x):
        """Passing data through the network.

        :param x: 2d tensor containing both (x,y) Variables
        :return: output of the net
        """

        features = self.conv(x).mean(dim=2)
        return self.dense(features)


class NCC(PairwiseModel):
    u"""Neural Causation Coefficient.

    Infer causal relationships between pairs of variables using mean embbedings of neural networks

    .. note:
        Ref :  Lopez-Paz, D. and Nishihara, R. and Chintala, S. and Schölkopf, B. and Bottou, L.,
        "Discovering Causal Signals in Images", CVPR 2017.

    """

    def __init__(self):
        super(NCC, self).__init__()
        self.model = None

    def fit(self, x_tr, y_tr, epochs=50, batchsize=32,
            learning_rate=0.01, verbose=None, device=None):
        """Fit the NCC model.

        Args:
            x_tr (pd.DataFrame): CEPC format dataframe containing the pairs
            y_tr (pd.DataFrame or np.ndarray): labels associated to the pairs
            epochs (int): number of train epochs
            learning_rate (float): learning rate of Adam
            verbose (bool): verbosity (defaults to ``cdt.SETTINGS.verbose``)
            device (str): cuda or cpu device (defaults to ``cdt.SETTINGS.default_device``)
        """
        if batchsize > len(x_tr):
            batchsize = len(x_tr)
        verbose, device = SETTINGS.get_default(('verbose', verbose),
                                               ('device', device))
        self.model = NCC_model()
        opt = th.optim.Adam(self.model.parameters(), lr=learning_rate)
        criterion = th.nn.BCEWithLogitsLoss()
        y = y_tr.values if isinstance(y_tr, pd.DataFrame) else y_tr
        y = th.Tensor(y)/2 + .5
        # print(y)
        self.model = self.model.to(device)
        y = y.to(device)
        dataset = []
        for i, (idx, row) in enumerate(x_tr.iterrows()):

            a = row['A'].reshape((len(row['A']), 1))
            b = row['B'].reshape((len(row['B']), 1))
            m = np.hstack((a, b))
            m = m.astype('float32')
            m = th.from_numpy(m).t().unsqueeze(0)
            dataset.append(m)
        dataset = [m.to(device) for m in dataset]
        acc = [0]
        da = th.utils.data.DataLoader(Dataset(dataset, y), batch_size=batchsize,
                                      shuffle=True)
        data_per_epoch = (len(dataset) // batchsize)
        with trange(epochs, desc="Epochs", disable=not verbose) as te:
            for epoch in te:
                with trange(data_per_epoch, desc="Batches of {}".format(batchsize),
                            disable=not (verbose and batchsize == len(dataset))) as t:
                    output = []
                    labels = []
                    for (batch, label), i in zip(da, t):
                        opt.zero_grad()
                        # print(batch.shape, labels.shape)
                        out = th.stack([self.model(m) for m in batch], 0).squeeze(2)
                        loss = criterion(out, label)
                        loss.backward()
                        t.set_postfix(loss=loss.item())
                        opt.step()
                        output.append(out)
                        labels.append(label)
                    acc = th.where(th.cat(output, 0) > .5,
                                   th.ones(len(output)),
                                   th.zeros(len(output))) - th.cat(labels, 0)
                    te.set_postfix(Acc=1-acc.abs().mean().item())

    def predict_proba(self, a, b, device=None):
        """Infer causal directions using the trained NCC pairwise model.

        Args:
            a (numpy.ndarray): Variable 1
            b (numpy.ndarray): Variable 2
            device (str): Device to run the algorithm on (defaults to ``cdt.SETTINGS.default_device``)

        Returns:
            float: Causation score (Value : 1 if a->b and -1 if b->a)
        """
        device = SETTINGS.get_default(device=device)
        if self.model is None:
            print('Model has to be trained before doing any predictions')
            raise ValueError
        if len(np.array(a).shape) == 1:
            a = np.array(a).reshape((-1, 1))
            b = np.array(b).reshape((-1, 1))
        m = np.hstack((a, b))
        m = scale(m)
        m = m.astype('float32')
        m = th.from_numpy(m).t().unsqueeze(0)

        if th.cuda.is_available():
            m = m.cuda()

        return (self.model(m).data.cpu().numpy()-.5) * 2

    def predict_dataset(self, df, device=None, verbose=None):
        """
        Args:
            x_tr (pd.DataFrame): CEPC format dataframe containing the pairs
            y_tr (pd.DataFrame or np.ndarray): labels associated to the pairs
            epochs (int): number of train epochs
            learning rate (float): learning rate of Adam
            verbose (bool): verbosity (defaults to ``cdt.SETTINGS.verbose``)
            device (str): cuda or cpu device (defaults to ``cdt.SETTINGS.default_device``)

        Returns:
            pandas.DataFrame: dataframe containing the predicted causation coefficients
        """
        verbose, device = SETTINGS.get_default(('verbose', verbose),
                                               ('device', device))
        dataset = []
        for i, (idx, row) in enumerate(df.iterrows()):
            a = row['A'].reshape((len(row['A']), 1))
            b = row['B'].reshape((len(row['B']), 1))
            m = np.hstack((a, b))
            m = m.astype('float32')
            m = th.from_numpy(m).t().unsqueeze(0)
            dataset.append(m)

        dataset = [m.to(device) for m in dataset]
        return pd.DataFrame((th.cat([self.model(m) for m, t in zip(dataset, trange(len(dataset)),
                                                      disable=not verbose)]\
                       , 0).data.cpu().numpy() -.5) * 2)
