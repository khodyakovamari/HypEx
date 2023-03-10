import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from scipy.stats import ks_2samp
from ..utils.psi_pandas import *

def smd(orig, matched):
    '''Standardized mean difference для проверки качества мэтчинга'''
    smd_data = abs(orig.mean(0) - matched.mean(0)) / orig.std(0)
    return smd_data


def ks(orig, matched):
    '''Тест Колмогорова-Смирнова для поколоночной проверки качества мэтчинга'''
    ks_dict = dict()
    matched.columns = orig.columns
    for col in orig.columns:
        ks_pval_1 = ks_2samp(orig[col].values, matched[col].values)[1]
        ks_dict.update({col: ks_pval_1})
    return ks_dict


def matching_quality(data, treatment, features, features_psi):
    ''' Функция обертка для оценки качества после получения мэтчинг с выводом результата в таблицу
        На вход принимает датасет, полученный после мэтчинга.
        data - df_matched, pd.DataFrame df_matched
        treatment -  treatment
        features - список фичей, kstest и smd принимает только числовые поля
        '''

    orig_treated = data[data[treatment] == 1][features]
    orig_untreated = data[data[treatment] == 0][features]
    matched_treated = data[data[treatment] == 1][
            [f + '_matched' for f in features]]
    matched_treated.columns = orig_treated.columns
    matched_untreated = data[data[treatment] == 0][
            [f + '_matched' for f in features]]
    matched_untreated.columns = orig_treated.columns

    psi_treated = data[data[treatment] == 1][features_psi]
    psi_treated_matched = data[data[treatment] == 1][[f + '_matched' for f in features_psi]]
    psi_treated_matched.columns = [f + '_treated' for f in features_psi]
    psi_treated.columns = [f + '_treated' for f in features_psi]
    psi_untreated = data[data[treatment] == 0][features_psi]
    psi_untreated_matched = data[data[treatment] == 0][
            [f + '_matched' for f in features_psi]]
    psi_untreated.columns = [f + '_untreated' for f in features_psi]
    psi_untreated_matched.columns = [f + '_untreated' for f in features_psi]
    treated_smd_data = smd(orig_treated, matched_treated)
    untreated_smd_data = smd(orig_untreated, matched_untreated)
    smd_data = pd.concat([treated_smd_data, untreated_smd_data], axis=1)
    smd_data.columns = ['match_control_to_treat', 'match_treat_to_control']
    treated_ks = ks(orig_treated, matched_treated)
    untreated_ks = ks(orig_untreated, matched_untreated)
    ks_dict = {k: [treated_ks[k], untreated_ks[k]] for k in treated_ks.keys()}
    ks_df = pd.DataFrame(data=ks_dict, index=range(2)).T
    ks_df.columns = ['match_control_to_treat', 'match_treat_to_control']
    report_psi_treated = report(psi_treated, psi_treated_matched)[['column', 'anomaly_score', 'check_result']]
    report_psi_untreated = report(psi_untreated, psi_untreated_matched)[['column', 'anomaly_score', 'check_result']]
    report_psi = pd.concat([report_psi_treated.reset_index(drop=True),report_psi_untreated.reset_index(drop=True)], axis=1)
    return report_psi, ks_df, smd_data