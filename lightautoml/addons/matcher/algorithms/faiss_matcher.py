import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression
import faiss
from scipy.stats import norm
from ..utils.metrics import *
from ..utils.psi_pandas import *

POSTFIX = "_matched"
POSTFIX_BIAS = "_matched_bias"


class FaissMatcher:
    def __init__(self,
                 df,
                 data,
                 outcomes,
                 treatment,
                 features=None,
                 group_col=False,
                 sigma=1.96,
                 validation=None):
        self.df = df
        self.data = data
        self.outcomes = outcomes
        self.treatment = treatment
        if features is None:
            self.feature_list = list(self.df.columns)
            self.feature_list.remove(self.treatment)
            self.feature_list.remove(self.outcomes)
        else:
            self.feature_list = features['Feature'].tolist()
        self.features_quality = self.df.drop(columns=[self.treatment, self.outcomes]).select_dtypes(
            include=['int16', 'int32', 'int64', 'float16', 'float32', 'float64']).columns
        self.dict_outcome_untreated = {}
        self.dict_outcome_treated = {}
        self.group_col = group_col
        self.treated_index = None
        self.untreated_index = None
        self.orig_treated_index = None
        self.orig_untreated_index = None
        self.results = {}
        self.ATE = None
        self.n_features = None
        self.df_matched = None
        self.sigma = sigma
        self.quality_dict = {}
        self.validation = validation

    def _get_split_scalar_data(self, df):
        std_scaler = StandardScaler().fit(df.drop([self.outcomes, self.treatment], axis=1))

        treated = df[df[self.treatment] == 1].drop([self.outcomes, self.treatment], axis=1)
        untreated = df[df[self.treatment] == 0].drop([self.outcomes, self.treatment], axis=1)

        std_treated = pd.DataFrame(std_scaler.transform(treated))
        std_untreated = pd.DataFrame(std_scaler.transform(untreated))

        return treated, untreated, std_treated, std_untreated

    def _transform_to_np(self, df):
        x = df.to_numpy().copy(order='C').astype("float32")
        whiten = faiss.PCAMatrix(x.shape[1], x.shape[1])
        whiten.train(x)
        faiss.vector_to_array(whiten.eigenvalues)
        xt = whiten.apply_py(x)
        return xt

    def _get_index(self, base, new):
        print("Creating index")
        index = faiss.IndexFlatL2(base.shape[1])
        print("Adding index")
        index.add(base)
        print("Finding index")
        indexes = index.search(new, 1)[1]
        return indexes

    def _predict_outcome(self, std_treated, std_untreated):
        self.dict_outcome_untreated = {}
        self.dict_outcome_treated = {}
        for outcome in [self.outcomes]:
            if self.group_col is None:
                y_untreated = self.df[self.df[self.treatment] == 0][outcome]
                y_treated = self.df[self.df[self.treatment] == 1][outcome]
                x_treated = std_treated
                x_untreated = std_untreated
                y_match_untreated = y_untreated.iloc[self.treated_index.ravel()]
                y_match_treated = y_treated.iloc[self.untreated_index.ravel()]
                x_match_treated = x_untreated.iloc[self.treated_index.ravel()]
                x_match_untreated = x_treated.iloc[self.untreated_index.ravel()]
            else:
                y_untreated = self.df.loc[self.orig_untreated_index.ravel()][outcome]
                y_treated = self.df.loc[self.orig_treated_index.ravel()][outcome]
                x_treated = self.df.loc[self.orig_treated_index.ravel()].drop(
                    columns=[self.treatment, outcome, self.group_col])
                x_untreated = self.df.loc[self.orig_untreated_index.ravel()].drop(
                    columns=[self.treatment, outcome, self.group_col])
                y_match_treated = self.df.loc[self.untreated_index.ravel()][outcome]
                y_match_untreated = self.df.loc[self.treated_index.ravel()][outcome]
                x_match_treated = self.df.loc[self.treated_index.ravel()].drop(
                    columns=[self.treatment, outcome, self.group_col])
                x_match_untreated = self.df.loc[self.untreated_index.ravel()].drop(
                    columns=[self.treatment, outcome, self.group_col])

            ols0 = LinearRegression().fit(x_untreated, y_untreated)
            ols1 = LinearRegression().fit(x_treated, y_treated)

            bias0 = ols0.predict(x_treated) - ols0.predict(x_match_treated)
            y_match_untreated_bias = y_match_untreated - bias0

            bias1 = ols1.predict(x_untreated) - ols1.predict(x_match_untreated)
            y_match_treated_bias = y_match_treated - bias1

            self.dict_outcome_untreated[outcome] = y_untreated.values
            self.dict_outcome_untreated[outcome + POSTFIX] = y_match_treated.values
            self.dict_outcome_untreated[outcome + POSTFIX_BIAS] = y_match_treated_bias.values

            self.dict_outcome_treated[outcome] = y_treated.values
            self.dict_outcome_treated[outcome + POSTFIX] = y_match_untreated.values
            self.dict_outcome_treated[outcome + POSTFIX_BIAS] = y_match_untreated_bias.values

        return

    def _create_outcome_matched_df(self, dict_outcome, is_treated: bool):
        df_pred = pd.DataFrame(dict_outcome)
        df_pred[self.treatment] = int(is_treated)
        df_pred[self.treatment + POSTFIX] = int(not is_treated)
        return df_pred

    def _create_features_matched_df(self, index, is_treated: bool):
        if self.group_col is None:
            x1 = self.data[self.data[self.treatment] == int(not is_treated)].iloc[index].reset_index()
            x2 = self.data[self.data[self.treatment] == int(is_treated)].reset_index()
        else:
            self.data = self.data.sort_values(self.group_col)
            x1 = self.data.loc[index].reset_index()
            if is_treated:
                x2 = self.data.loc[self.orig_treated_index].reset_index()
            else:
                x2 = self.data.loc[self.orig_untreated_index].reset_index()
        x1.columns = [col + POSTFIX for col in x2.columns]

        x = pd.concat([x2, x1], axis=1).drop([self.treatment, self.treatment + POSTFIX], axis=1)
        return x

    def _create_matched_df(self):

        df_pred0 = self._create_outcome_matched_df(self.dict_outcome_untreated, False)
        df_pred1 = self._create_outcome_matched_df(self.dict_outcome_treated, True)

        df_matched = pd.concat([df_pred0, df_pred1])

        x_ = self._create_features_matched_df(self.treated_index.ravel(), True)
        x = self._create_features_matched_df(self.untreated_index.ravel(), False)

        x = pd.concat([x_, x])
        df_matched = pd.concat([x.reset_index(drop=True), df_matched.reset_index(drop=True)], axis=1)
        return df_matched

    def calc_ate(self, df, outcome):
        ate = np.mean(
            (2 * df[self.treatment] - 1) * (df[outcome] - df[outcome + POSTFIX_BIAS]))
        return ate

    def calc_atc(self, df, outcome):
        '''
        Рассчет АТС - эффект на контрольной группе, если бы на неё было оказано воздействие
        '''
        df = df[df[self.treatment] == 0]
        N_c = len(df)
        index_c = list(range(N_c))
        ITT_c = df[outcome + POSTFIX_BIAS] - df[outcome]
        if self.group_col is None:
            scaled_counts_c = self.scaled_counts(N_c, self.treated_index, index_c)
        else:
            scaled_counts_c = self.scaled_counts(N_c, self.treated_index, self.orig_untreated_index)
        vars_c = np.repeat(ITT_c.var(), N_c)  # conservative
        atc = np.mean(ITT_c)
        return atc, scaled_counts_c, vars_c

    def calc_att(self, df, outcome):
        '''Рассчет АТТ - эффект от пилота'''
        df = df[df[self.treatment] == 1]
        N_t = len(df)
        index_t = list(range(N_t))
        ITT_t = df[outcome] - df[outcome + POSTFIX_BIAS]
        if self.group_col is None:
            scaled_counts_t = self.scaled_counts(N_t, self.untreated_index, index_t)
        else:
            scaled_counts_t = self.scaled_counts(N_t, self.untreated_index, self.orig_treated_index)
        vars_t = np.repeat(ITT_t.var(), N_t)  # conservative
        att = np.mean(ITT_t)
        return att, scaled_counts_t, vars_t

    def scaled_counts(self, N, matches, index):

        # Counts the number of times each subject has appeared as a match. In
        # the case of multiple matches, each subject only gets partial credit.

        s_counts = np.zeros(N)
        index_dict = dict(zip(index, list(range(N))))
        for matches_i in matches:
            scale = 1 / len(matches_i)
            for match in matches_i:
                s_counts[index_dict[match]] += scale

        return s_counts

    def calc_atx_var(self, vars_c, vars_t, weights_c, weights_t):
        # ATE дисперсия
        N_c, N_t = len(vars_c), len(vars_t)
        summands_c = weights_c ** 2 * vars_c
        summands_t = weights_t ** 2 * vars_t

        return summands_t.sum() / N_t ** 2 + summands_c.sum() / N_c ** 2

    def calc_atc_se(self, vars_c, vars_t, scaled_counts_t):
        # ATС стандартная ошибка
        N_c, N_t = len(vars_c), len(vars_t)
        weights_c = np.ones(N_c)
        weights_t = (N_t / N_c) * scaled_counts_t

        var = self.calc_atx_var(vars_c, vars_t, weights_c, weights_t)

        return np.sqrt(var)

    def calc_att_se(self, vars_c, vars_t, scaled_counts_c):
        # ATT стандартная ошибка
        N_c, N_t = len(vars_c), len(vars_t)
        weights_c = (N_c / N_t) * scaled_counts_c
        weights_t = np.ones(N_t)

        var = self.calc_atx_var(vars_c, vars_t, weights_c, weights_t)

        return np.sqrt(var)

    def calc_ate_se(self, vars_c, vars_t, scaled_counts_c, scaled_counts_t):
        # ATE стандартная ошибка
        N_c, N_t = len(vars_c), len(vars_t)
        N = N_c + N_t
        weights_c = (N_c / N) * (1 + scaled_counts_c)
        weights_t = (N_t / N) * (1 + scaled_counts_t)

        var = self.calc_atx_var(vars_c, vars_t, weights_c, weights_t)

        return np.sqrt(var)

    def pval_calc(self, z):
        # P-value
        return round(2 * (1 - norm.cdf(abs(z))), 2)

    def _calculate_ate_all_target(self, df):
        att_dict = {}
        atc_dict = {}
        ate_dict = {}
        for outcome in [self.outcomes]:
            ate = self.calc_ate(df, outcome)
            att, scaled_counts_t, vars_t = self.calc_att(df, outcome)
            atc, scaled_counts_c, vars_c = self.calc_atc(df, outcome)
            att_se = self.calc_att_se(vars_c, vars_t, scaled_counts_c)
            atc_se = self.calc_atc_se(vars_c, vars_t, scaled_counts_t)
            ate_se = self.calc_ate_se(vars_c, vars_t, scaled_counts_c, scaled_counts_t)
            ate_dict[outcome] = [ate, ate_se, self.pval_calc(ate / ate_se), ate - self.sigma * ate_se,
                                 ate + self.sigma * ate_se]
            atc_dict[outcome] = [atc, atc_se, self.pval_calc(atc / atc_se), atc - self.sigma * atc_se,
                                 atc + self.sigma * atc_se]
            att_dict[outcome] = [att, att_se, self.pval_calc(att / att_se), att - self.sigma * att_se,
                                 att + self.sigma * att_se]
        return ate_dict, atc_dict, att_dict

    def _check_best(self, df_matched, n_features):
        ate_dict, atc_dict, att_dict = self._calculate_ate_all_target(df_matched)

        if self.validation is not None:
            self.val_dict = ate_dict
            return

        if self.n_features is None:
            self.n_features = n_features
            self.ATE = ate_dict
            self.ATC = atc_dict
            self.ATT = att_dict
            self.df_matched = df_matched
            return

        diffkeys = sum([1 if ate_dict[k] > self.ATE[k] else -1 for k in ate_dict])

        if diffkeys > 0:
            self.n_features = n_features
            self.ATE = ate_dict
            self.df_matched = df_matched

        if diffkeys == 0:
            if np.array(list(ate_dict.values())).mean() > np.array(list(self.ATE.values())).mean():
                self.n_features = n_features
                self.ATE = ate_dict
                self.df_matched = df_matched

    def matching_quality(self):
        '''
        Method for estimate the quality of covariates balance.
        Estimates population stability index, Standartizied mean difference
        and Kolmogorov-Smirnov test for numeric values. Returns dict of reports.
         '''

        psi_columns = self.data.drop(columns=[self.treatment]).columns
        psi_data, ks_data, smd_data = matching_quality(self.df_matched, self.treatment, self.features_quality,
                                                       psi_columns)
        self.quality_dict = {'psi': psi_data, 'ks_test': ks_data, 'smd': smd_data}
        print("kek", self.quality_dict)
        return self.quality_dict

    def group_match(self):
        self.df = self.df.sort_values(self.group_col)
        groups = sorted(self.df[self.group_col].unique())
        all_treated_matches = {}
        all_untreated_matches = {}
        all_treated_outcome = {}
        all_untreated_outcome = {}
        for group in groups:
            df = self.df[self.df[self.group_col] == group]
            treated_index = {}
            untreated_index = {}
            temp = df[self.feature_list + [self.treatment] + [self.outcomes] + [self.group_col]]
            temp = temp.loc[:, (temp != 0).any(axis=0)].drop(columns=self.group_col)
            treated, untreated, std_treated, std_untreated = self._get_split_scalar_data(temp)
            for i, ind in enumerate(temp[temp[self.treatment] == 1].index):
                treated_index.update({i: ind})
            for i, ind in enumerate(temp[temp[self.treatment] == 0].index):
                untreated_index.update({i: ind})

            std_treated_np = self._transform_to_np(std_treated)
            std_untreated_np = self._transform_to_np(std_untreated)
            matches_c = self._get_index(std_treated_np, std_untreated_np)
            matches_t = self._get_index(std_untreated_np, std_treated_np)
            matches_c = np.array([list(map(lambda x: treated_index[x], l)) for l in matches_c])
            matches_t = np.array([list(map(lambda x: untreated_index[x], l)) for l in matches_t])
            all_treated_matches.update({group: matches_t})
            all_untreated_matches.update({group: matches_c})
            all_treated_outcome.update({group: list(treated_index.values())})
            all_untreated_outcome.update({group: list(untreated_index.values())})
        matches_c = [item for sublist in [i.tolist() for i in list(all_untreated_matches.values())] for item in sublist]
        matches_t = [item for sublist in [i.tolist() for i in list(all_treated_matches.values())] for item in sublist]
        index_c = [item for sublist in [i for i in list(all_untreated_outcome.values())] for item in sublist]
        index_t = [item for sublist in [i for i in list(all_treated_outcome.values())] for item in sublist]
        self.untreated_index = np.array(matches_c)
        self.treated_index = np.array(matches_t)
        self.orig_treated_index = np.array(index_t)
        self.orig_untreated_index = np.array(index_c)
        df = self.df[self.feature_list + [self.treatment] + [self.outcomes]]
        treated, untreated, std_treated, std_untreated = self._get_split_scalar_data(df)
        self._predict_outcome(treated, untreated)
        df_matched = self._create_matched_df()
        self._check_best(df_matched, 10)

        if self.validation:
            return self.val_dict

        return self.df_matched, (self.ATE, self.ATC, self.ATT)

    def match(self):
        for i in range(4, 10):
            df = self.df[self.feature_list[:i] + [self.treatment] + [self.outcomes]]
            treated, untreated, std_treated, std_untreated = self._get_split_scalar_data(df)

            std_treated_np = self._transform_to_np(std_treated)
            std_untreated_np = self._transform_to_np(std_untreated)

            untreated_index = self._get_index(std_treated_np, std_untreated_np)
            treated_index = self._get_index(std_untreated_np, std_treated_np)

            self.untreated_index = untreated_index
            self.treated_index = treated_index

            self._predict_outcome(treated, untreated)

            df_matched = self._create_matched_df()
            self._check_best(df_matched, i)

        if self.validation:
            return self.val_dict

        return self.df_matched, (self.ATE, self.ATC, self.ATT)
