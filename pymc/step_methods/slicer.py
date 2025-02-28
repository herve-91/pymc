#   Copyright 2020 The PyMC Developers
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

# Modified from original implementation by Dominik Wabersich (2013)

from typing import Tuple

import numpy as np
import numpy.random as nr

from pymc.blocking import RaveledVars, StatsType
from pymc.model import modelcontext
from pymc.step_methods.arraystep import ArrayStep, Competence
from pymc.util import get_value_vars_from_user_vars
from pymc.vartypes import continuous_types

__all__ = ["Slice"]

LOOP_ERR_MSG = "max slicer iters %d exceeded"


class Slice(ArrayStep):
    """
    Univariate slice sampler step method

    Parameters
    ----------
    vars: list
        List of value variables for sampler.
    w: float
        Initial width of slice (Defaults to 1).
    tune: bool
        Flag for tuning (Defaults to True).
    model: PyMC Model
        Optional model for sampling step. Defaults to None (taken from context).

    """

    name = "slice"
    default_blocked = False
    stats_dtypes = [
        {
            "nstep_out": int,
            "nstep_in": int,
        }
    ]

    def __init__(self, vars=None, w=1.0, tune=True, model=None, iter_limit=np.inf, **kwargs):
        self.model = modelcontext(model)
        self.w = w
        self.tune = tune
        self.n_tunes = 0.0
        self.iter_limit = iter_limit

        if vars is None:
            vars = self.model.continuous_value_vars
        else:
            vars = get_value_vars_from_user_vars(vars, self.model)

        super().__init__(vars, [self.model.compile_logp()], **kwargs)

    def astep(self, apoint: RaveledVars, *args) -> Tuple[RaveledVars, StatsType]:
        # The arguments are determined by the list passed via `super().__init__(..., fs, ...)`
        logp = args[0]
        q0_val = apoint.data
        self.w = np.resize(self.w, len(q0_val))  # this is a repmat

        nstep_out = nstep_in = 0

        q = np.copy(q0_val)
        ql = np.copy(q0_val)  # l for left boundary
        qr = np.copy(q0_val)  # r for right boudary

        # The points are not copied, so it's fine to update them inplace in the
        # loop below
        q_ra = RaveledVars(q, apoint.point_map_info)
        ql_ra = RaveledVars(ql, apoint.point_map_info)
        qr_ra = RaveledVars(qr, apoint.point_map_info)

        for i, wi in enumerate(self.w):
            # uniformly sample from 0 to p(q), but in log space
            y = logp(q_ra) - nr.standard_exponential()

            # Create initial interval
            ql[i] = q[i] - nr.uniform() * wi  # q[i] + r * w
            qr[i] = ql[i] + wi  # Equivalent to q[i] + (1-r) * w

            # Stepping out procedure
            cnt = 0
            while y <= logp(ql_ra):  # changed lt to leq  for locally uniform posteriors
                ql[i] -= wi
                cnt += 1
                if cnt > self.iter_limit:
                    raise RuntimeError(LOOP_ERR_MSG % self.iter_limit)
            nstep_out += cnt

            cnt = 0
            while y <= logp(qr_ra):
                qr[i] += wi
                cnt += 1
                if cnt > self.iter_limit:
                    raise RuntimeError(LOOP_ERR_MSG % self.iter_limit)
            nstep_out += cnt

            cnt = 0
            q[i] = nr.uniform(ql[i], qr[i])
            while y > logp(q_ra):  # Changed leq to lt, to accomodate for locally flat posteriors
                # Sample uniformly from slice
                if q[i] > q0_val[i]:
                    qr[i] = q[i]
                elif q[i] < q0_val[i]:
                    ql[i] = q[i]
                q[i] = nr.uniform(ql[i], qr[i])
                cnt += 1
                if cnt > self.iter_limit:
                    raise RuntimeError(LOOP_ERR_MSG % self.iter_limit)
            nstep_in += cnt

            if self.tune:
                # I was under impression from MacKays lectures that slice width can be tuned without
                # breaking markovianness. Can we do it regardless of self.tune?(@madanh)
                self.w[i] = wi * (self.n_tunes / (self.n_tunes + 1)) + (qr[i] - ql[i]) / (
                    self.n_tunes + 1
                )

            # Set qr and ql to the accepted points (they matter for subsequent iterations)
            qr[i] = ql[i] = q[i]

        if self.tune:
            self.n_tunes += 1

        stats = {
            "nstep_out": nstep_out,
            "nstep_in": nstep_in,
        }

        return RaveledVars(q, apoint.point_map_info), [stats]

    @staticmethod
    def competence(var, has_grad):
        if var.dtype in continuous_types:
            if not has_grad and var.ndim == 0:
                return Competence.PREFERRED
            return Competence.COMPATIBLE
        return Competence.INCOMPATIBLE
