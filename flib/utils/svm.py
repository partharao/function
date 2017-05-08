import argparse
import numpy as np
import random
from sklearn.svm import SVC, LinearSVC
from sklearn.model_selection import train_test_split, cross_val_score, cross_val_predict, GridSearchCV, KFold
from sklearn.metrics import roc_auc_score, classification_report
from sklearn.feature_selection import VarianceThreshold

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
                                help='output prediction file')
parser.add_argument('--gmt', '-g', dest='gmt', type=str,
                                help='input GMT (geneset) file')
parser.add_argument('--all', '-a', dest='all', action='store_true',
                                default=False,
                                help='predict all genes')
parser.add_argument('--best-params', '-b', dest='best_params', action='store_true',
                                default=False,
                                help='select best parameters by cross validation')
parser.add_argument('--geneset_id', '-G', dest='geneset_id', type=str,
                                help='geneset id')




args = parser.parse_args()

dab = Dab(args.input)

if args.gmt:
    gmt = GMT(filename=args.gmt)
    pos_genes = gmt.get_genes(args.geneset_id)
    neg_genes = gmt.genes - pos_genes
else:
    # Load OMIM annotations
    do = DiseaseOntology.generate()
    omim = OMIM()
    omim.load_onto(onto=do)
    do.propagate()
    term = do.get_term(args.geneset_id)

    pos_genes = set(term.get_annotated_genes())

    all_genes = set()
    for term in do.get_termobject_list():
        all_genes |= set(term.get_annotated_genes())
    neg_genes = all_genes - pos_genes

# Group training genes
train_genes = [g for g in (pos_genes | neg_genes) if dab.get_index(g) is not None]
train_genes_idx = [dab.get_index(g) for g in train_genes]


if args.all:
    # Load dab as matrix
    X_all = np.empty([dab.get_size(), dab.get_size()])
    for i, g in enumerate(dab.gene_list):
        if not i % 1000:
            print i
        X_all[i] = dab.get(g)

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
    # Split the dataset in two equal parts
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0, random_state=0)

    # Set the parameters by cross-validation
    tuned_parameters = [
        {'C': [1, 10, 100, 1000], 'class_weight':['balanced', None]},
        #{'C': [1, 10, 100, 1000, 10000], 'class_weight':['balanced', None], 'loss':['l2'], 'penalty':['l1'], 'dual':[False]}
    ]

    score = 'roc_auc'

    print("# Tuning hyper-parameters for %s" % score)

    clf = GridSearchCV(LinearSVC(C=1), tuned_parameters, cv=3, n_jobs=15,
                       scoring=score)
    clf.fit(X_train, y_train)
    best_params = clf.best_params_

    print(clf.best_params_)
    means = clf.cv_results_['mean_test_score']
    stds = clf.cv_results_['std_test_score']
    for mean, std, params in zip(means, stds, clf.cv_results_['params']):
        print("%0.3f (+/-%0.03f) for %r"
              % (mean, std * 2, params))
else:
    best_params = {'C':50, 'class_weight':'balanced'}


train_scores = np.empty(len(train_genes))
train_scores[:] = np.NAN
scores = None

kf = KFold(n_splits=5)
for train, test in kf.split(X):
    X_train, X_test, y_train, y_test = X[train], X[test], y[train], y[test]

    print "Learning SVM"
    #clf = LinearSVC(C=1000, class_weight='balanced', dual=False)
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
        print roc_auc_score(y_test, scores_cv)

        for i,idx in enumerate(test):
            train_scores[idx] = scores_cv[i]

#print scores
#scores = np.median(scores, axis=1)
#print scores
#print train_scores
print len(pos_genes & set(train_genes)), len(neg_genes & set(train_genes)), roc_auc_score(y, train_scores)

if args.output:
    with open(args.output, 'w') as outfile:
        for (g,s) in zip(train_genes, train_scores):
            line = [g, ('1' if g in pos_genes else '-1'), str(s), '\n']
            outfile.write('\t'.join(line))
        outfile.close()