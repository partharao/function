import argparse
import numpy as np
import random
import os
from collections import namedtuple
from operator import itemgetter

from sklearn.svm import SVC, LinearSVC
from sklearn.model_selection import train_test_split, cross_val_score, cross_val_predict, GridSearchCV, KFold
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import label_binarize
from sklearn.metrics import roc_auc_score, classification_report, average_precision_score, brier_score_loss
from sklearn.feature_selection import VarianceThreshold
from sklearn.ensemble import BaggingClassifier
from sklearn.calibration import CalibratedClassifierCV, _SigmoidCalibration
from sklearn.isotonic import IsotonicRegression

from flib.core.dab import Dab
from flib.core.omim import OMIM
from flib.core.hgmd import HGMD
from flib.core.onto import DiseaseOntology
from flib.core.entrez import Entrez
from flib.core.gmt import GMT

parser = argparse.ArgumentParser(description='Generate a file of updated disease gene annotations')
parser.add_argument('--input', '-i', dest='input', type=str,
                                help='input dab file')
parser.add_argument('--output', '-o', dest='output', type=str,
                                help='output directory')
parser.add_argument('--gmt', '-g', dest='gmt', type=str,
                                help='input GMT (geneset) file')
parser.add_argument('--dir', '-d', dest='dir', type=str,
                                help='directory of labels')
parser.add_argument('--all', '-a', dest='all', action='store_true',
                                default=False,
                                help='predict all genes')
parser.add_argument('--best-params', '-b', dest='best_params', action='store_true',
                                default=False,
                                help='select best parameters by cross validation')
parser.add_argument('--geneset_id', '-G', dest='geneset_id', type=str,
                                help='geneset id')
parser.add_argument('--prob', '-p', dest='prob_fit',
                                choices=['SIGMOID','ISO'],
                                default=None,
                                help='probability fit')

args = parser.parse_args()

standards = {}
Std = namedtuple('Std', ['pos', 'neg'])

if args.gmt:
    gmt = GMT(filename=args.gmt)
    if args.geneset_id:
        pos_genes = gmt.get_genes(args.geneset_id)
        neg_genes = gmt.genes - pos_genes
        standards[args.geneset_id] = Std(pos=pos_genes, neg=neg_genes)
    else:
        for (gsid, genes) in gmt.genesets.iteritems():
            pos_genes = gmt.get_genes(gsid)
            neg_genes = gmt.genes - pos_genes
            if len(pos_genes) >= 10 and len(pos_genes) <= 1000:
                standards[gsid] = Std(pos=pos_genes, neg=neg_genes)
elif args.dir:
    for f in os.listdir(args.dir):
        pos_genes, neg_genes = set(), set()
        with open(args.dir + '/' + f) as labelf:
            lines = labelf.readlines()
            for l in lines:
                gene, label = l.strip('\t').split()[:2]
                if label == '1':
                    pos_genes.add(gene)
                elif label == '-1':
                    neg_genes.add(gene)

        if len(pos_genes) < 500:
            standards[f] = Std(pos=pos_genes, neg=neg_genes)

dab = Dab(args.input)
if args.all:
    # Load dab as matrix
    X_all = np.empty([dab.get_size(), dab.get_size()])
    for i, g in enumerate(dab.gene_list):
        if not i % 1000:
            print i
        X_all[i] = dab.get(g)

for gsid, std in standards.iteritems():

    if args.output and os.path.exists(args.output + '/' + gsid):
        continue

    print 'Predicting', gsid, len(std.pos), len(std.neg)
    pos_genes, neg_genes = std.pos, std.neg

    # Group training genes
    train_genes = [g for g in (pos_genes | neg_genes) if dab.get_index(g) is not None]
    train_genes_idx = [dab.get_index(g) for g in train_genes]

    if args.all:
        # Subset training matrix and labels
        X = X_all[train_genes_idx]
        y = np.array([1 if g in pos_genes else -1 for g in train_genes])
    else:
        X = np.empty([len(train_genes), dab.get_size()])
        y = np.empty(len(train_genes))
        for i, g in enumerate(train_genes):
            X[i] = dab.get(g)
            y[i] = 1 if g in pos_genes else -1

    if args.best_params:
        # Set the parameters by cross-validation
        tuned_parameters = [
            {'C': [.0001, .001, .01, .1, 1, 10, 100], 'class_weight':['balanced', None]},
        ]
        score = 'average_precision'

        print("# Tuning hyper-parameters for %s" % score)

        clf = GridSearchCV(LinearSVC(), tuned_parameters, cv=3, n_jobs=10,
                           scoring=score)
        clf.fit(X, y)
        best_params = clf.best_params_

        print(clf.best_params_)
        means = clf.cv_results_['mean_test_score']
        stds = clf.cv_results_['std_test_score']
        for mean, std, params in zip(means, stds, clf.cv_results_['params']):
            print("%0.3f (+/-%0.03f) for %r"
                  % (mean, std * 2, params))
    else:
        best_params = {'C':50, 'class_weight':'balanced'}

    train_scores, train_probs = np.empty(len(train_genes)), np.empty(len(train_genes))
    train_scores[:], train_probs[:] = np.NAN, np.NAN
    scores, probs = None, None

    kf = StratifiedKFold(n_splits=5)
    for cv, (train, test) in enumerate(kf.split(X, y)):
        X_train, X_test, y_train, y_test = X[train], X[test], y[train], y[test]

        print "Learning SVM"
        clf = LinearSVC(**best_params)
        clf.fit(X_train, y_train)

        print "Predicting SVM"
        if args.all:
            scores_cv = clf.decision_function(X_all)
            scores = scores_cv if scores is None else np.column_stack((scores, scores_cv))

            for idx in test:
                train_scores[idx] = scores_cv[train_genes_idx[idx]]
        else:
            scores_cv = clf.decision_function(X_test)
            for i,idx in enumerate(test):
                train_scores[idx] = scores_cv[i]

    if args.prob_fit == 'ISO':
        ir = IsotonicRegression(out_of_bounds='clip')
        Y = label_binarize(y, [-1,1])
        ir.fit(train_scores, Y[:,0])
        train_probs = ir.predict(train_scores)
    elif args.prob_fit == 'SIGMOID':
        Y = label_binarize(y, [-1,1])
        sc = _SigmoidCalibration()
        sc.fit(train_scores, Y)
        train_probs = sc.predict(train_scores)

    if args.all:
        scores = np.median(scores, axis=1)
        for i,idx in enumerate(train_genes_idx):
            scores[idx] = train_scores[i]

        probs = np.median(probs, axis=1)
        for i,idx in enumerate(train_genes_idx):
            probs[idx] = train_probs[i]

        genes = dab.gene_list
    else:
        scores = train_scores
        genes = train_genes
        probs = train_probs

    print 'Performance:', \
        len(neg_genes & set(train_genes)), \
        roc_auc_score(y, train_scores), \
        roc_auc_score(y, train_probs), \
        average_precision_score(y, train_scores), \
        average_precision_score(y, train_probs)

    if args.output:
        sorted_scores = sorted(zip(genes, scores, probs), key=itemgetter(1), reverse=True)
        with open(args.output + '/' + gsid, 'w') as outfile:
            for (g,s,p) in sorted_scores:
                if g in pos_genes:
                    label = '1'
                elif g in neg_genes:
                    label = '-1'
                else:
                    label = '0'
                line = [g, label, str(s), str(p), '\n']
                outfile.write('\t'.join(line))
            outfile.close()
