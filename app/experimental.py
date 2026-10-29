"""Deterministic regression with explicit statistical and physical boundaries."""
import math
from typing import Literal

import numpy as np
import pint
from pydantic import Field, model_validator
from scipy.optimize import least_squares
from scipy.stats import norm, t, f as f_distribution

from app.schemas import Inputs

Model = Literal["linear", "first_order_decay", "first_order_uptake", "power_law", "arrhenius"]
MODELS = {
    "linear": "y = intercept + slope*x",
    "first_order_decay": "y = amplitude*exp(-rate*x)",
    "first_order_uptake": "y = amplitude*(1-exp(-rate*x))",
    "power_law": "y = amplitude*(x/(1 declared_x_unit))**exponent",
    "arrhenius": "y = k_ref*exp[-Ea/R*(1/T-1/T_ref)]",
}
_units = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
R = 8.31446261815324


class FitInput(Inputs):
    x: list[float] = Field(min_length=4, max_length=5000)
    y: list[float] = Field(min_length=4, max_length=5000)
    model: Model = "linear"
    x_unit: str = Field(default="dimensionless", min_length=1, max_length=60)
    y_unit: str = Field(default="dimensionless", min_length=1, max_length=60)
    sigma_y: list[float] | None = Field(default=None, min_length=4, max_length=5000)
    absolute_sigma: bool = False
    initial: list[float] | None = Field(default=None, min_length=2, max_length=2)
    confidence: float = Field(default=0.95, ge=0.5, le=0.999)
    prediction_x: list[float] = Field(default_factory=list, max_length=200)
    allow_extrapolation: bool = False

    @model_validator(mode="after")
    def check_data(self):
        if len(self.x) != len(self.y) or (self.sigma_y is not None and len(self.sigma_y) != len(self.x)):
            raise ValueError("x、y 和 sigma_y 行数必须一致")
        if len(set(self.x)) < 3:
            raise ValueError("至少需要3个不同的自变量取值")
        if self.sigma_y is not None and any(v <= 0 for v in self.sigma_y):
            raise ValueError("观测标准差 sigma_y 必须为正")
        if self.absolute_sigma and self.sigma_y is None:
            raise ValueError("已知绝对误差模式必须提供 sigma_y")
        try:
            _units.Unit(self.x_unit)
            _units.Unit(self.y_unit)
        except (ValueError, pint.PintError) as exc:
            raise ValueError("无法识别数据单位") from exc
        if self.prediction_x and not self.allow_extrapolation:
            if min(self.prediction_x) < min(self.x) or max(self.prediction_x) > max(self.x):
                raise ValueError("预测点超出数据范围；外推需显式启用 allow_extrapolation")
        return self


def model_values(model, x, theta, reference_K=None):
    """Return predictions and exact Jacobian in the reported parameterization."""
    a, b = theta
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        if model == "linear":
            return a + b*x, np.column_stack((np.ones_like(x), x))
        if model in {"first_order_decay", "first_order_uptake"}:
            exponential = np.exp(-b*x)
            if model == "first_order_decay":
                return a*exponential, np.column_stack((exponential, -a*x*exponential))
            uptake = -np.expm1(-b*x)
            return a*uptake, np.column_stack((uptake, a*x*exponential))
        if model == "power_law":
            exponential = np.exp(b*np.log(x))
            return a*exponential, np.column_stack((exponential, a*exponential*np.log(x)))
        factor = -(1/x - 1/reference_K)/R
        exponential = np.exp(b*factor)
        return a*exponential, np.column_stack((exponential, a*exponential*factor))


def _prepared_x(p, values):
    x = np.asarray(values, dtype=float)
    if p.model == "arrhenius":
        try:
            x = np.asarray(_units.Quantity(x, p.x_unit).to("kelvin").magnitude)
        except pint.PintError as exc:
            raise ValueError("Arrhenius 自变量必须带温度单位，例如 K 或 degC") from exc
        if np.any(x <= 0):
            raise ValueError("绝对温度必须大于零")
    elif p.model in {"first_order_decay", "first_order_uptake"} and np.any(x < 0):
        raise ValueError("一级衰减/趋近模型要求 x≥0，时间原点必须由研究者定义")
    elif p.model == "power_law" and np.any(x <= 0):
        raise ValueError("幂律模型要求 x>0")
    return x


def _starts(p, x, y, reference):
    if p.initial is not None:
        return [np.asarray(p.initial, dtype=float)]
    slope, intercept = np.polyfit(x, y, 1)
    if p.model == "linear":
        return [np.array([intercept, slope])]
    amplitude = max(float(np.max(y)), float(np.ptp(y)), 1e-8)
    xmax = max(float(np.max(x)), 1e-12)
    if p.model == "first_order_uptake":
        return [np.array([amplitude*a, b/xmax]) for a, b in [(1.1, 1), (2, .1), (1, 10)]]
    if p.model == "first_order_decay":
        if np.all(y > 0):
            gradient, log_a = np.polyfit(x, np.log(y), 1)
            return [np.array([math.exp(float(np.clip(log_a, -100, 100))), max(-gradient, 1e-10)])]
        return [np.array([amplitude, 1/xmax])]
    if p.model == "power_law":
        if np.all(y > 0):
            b, log_a = np.polyfit(np.log(x), np.log(y), 1)
            return [np.array([math.exp(float(np.clip(log_a, -100, 100))), float(np.clip(b, -49, 49))])]
        return [np.array([amplitude, 1.0])]
    if np.all(y > 0):
        gradient, intercept = np.polyfit(1/x - 1/reference, np.log(y), 1)
        return [np.array([math.exp(float(np.clip(intercept, -100, 100))), float(np.clip(-gradient*R, -9e5, 9e5))])]
    return [np.array([amplitude, 50000.0])]


def fit_data(p: FitInput):
    x = _prepared_x(p, p.x)
    y = np.asarray(p.y)
    sigma = np.ones(len(x)) if p.sigma_y is None else np.asarray(p.sigma_y)
    reference = float(1/np.mean(1/x)) if p.model == "arrhenius" else None
    bounds = {
        "linear": ([-np.inf, -np.inf], [np.inf, np.inf]),
        "first_order_decay": ([0, 0], [np.inf, np.inf]),
        "first_order_uptake": ([0, 0], [np.inf, np.inf]),
        "power_law": ([0, -50], [np.inf, 50]),
        "arrhenius": ([0, -1e6], [np.inf, 1e6]),
    }[p.model]
    solutions = []
    for initial in _starts(p, x, y, reference):
        if np.any(initial < bounds[0]) or np.any(initial > bounds[1]):
            raise ValueError("初始参数超出模型约束")
        try:
            solution = least_squares(
                lambda theta: (model_values(p.model, x, theta, reference)[0] - y)/sigma,
                initial,
                jac=lambda theta: model_values(p.model, x, theta, reference)[1]/sigma[:, None],
                bounds=bounds, x_scale=np.maximum(np.abs(initial), 1e-8),
                ftol=1e-13, xtol=1e-13, gtol=1e-13, max_nfev=3000,
            )
            if solution.success and np.all(np.isfinite(solution.fun)):
                solutions.append(solution)
        except (ValueError, FloatingPointError):
            continue
    if not solutions:
        raise ValueError("回归未收敛或计算溢出；请检查模型适用性、数据尺度和初值")
    solution = min(solutions, key=lambda s: np.dot(s.fun, s.fun))
    theta = solution.x
    predicted, jacobian = model_values(p.model, x, theta, reference)
    residual = y - predicted
    ss = float(residual@residual)
    objective = float(solution.fun@solution.fun)
    n, parameter_count = len(x), len(theta)
    df = n-parameter_count
    param_scale = np.maximum(np.abs(theta), 1e-8)
    scaled_j = jacobian/sigma[:, None]*param_scale
    _, singular, vt = np.linalg.svd(scaled_j, full_matrices=False)
    rank = int(np.sum(singular > singular[0]*max(scaled_j.shape)*np.finfo(float).eps))
    warnings = [
        "拟合与置信区间不证明化学机理或因果关系；输入数据的实验来源须另行核实。",
        "局部线性误差传播假定独立观测、模型适用且自变量误差可忽略；不含模型结构误差。",
    ]
    covariance = None
    correlation = None
    active_bounds = bool(np.any(solution.active_mask != 0))
    if rank == parameter_count:
        covariance = (vt.T@np.diag(1/singular**2)@vt)*param_scale[:, None]*param_scale[None, :]
        if not p.absolute_sigma:
            covariance *= objective/df
        se = np.sqrt(np.maximum(np.diag(covariance), 0))
        if np.all(se > 0):
            correlation = float(covariance[0, 1]/(se[0]*se[1]))
            if abs(correlation) > .995:
                warnings.append("参数高度相关，单个参数可能难以辨识；应增加有区分度的实验条件。")
    else:
        warnings.append("参数雅可比秩不足，无法给出可靠的参数协方差或区间。")
    if active_bounds:
        warnings.append("解位于参数约束边界，常规对称置信区间不适用，区间已省略。")
        covariance = None
    quantile = float(norm.ppf((1+p.confidence)/2) if p.absolute_sigma else t.ppf((1+p.confidence)/2, df))
    parameter_names = {
        "linear": ["intercept", "slope"],
        "first_order_decay": ["amplitude", "rate"],
        "first_order_uptake": ["amplitude", "rate"],
        "power_law": ["amplitude", "exponent"],
        "arrhenius": ["k_ref", "Ea_J_mol"],
    }[p.model]
    parameter_units = [p.y_unit, f"({p.y_unit})/({p.x_unit})"] if p.model == "linear" else (
        [p.y_unit, f"1/({p.x_unit})"] if p.model in {"first_order_decay", "first_order_uptake"} else
        [p.y_unit, "dimensionless" if p.model == "power_law" else "J/mol"])
    parameters = []
    for i, name in enumerate(parameter_names):
        std = float(math.sqrt(max(covariance[i, i], 0))) if covariance is not None else None
        interval = [float(theta[i]-quantile*std), float(theta[i]+quantile*std)] if std is not None and not active_bounds else None
        parameters.append({"name": name, "value": float(theta[i]), "unit": parameter_units[i], "standard_error": std, "confidence_interval": interval})
    total_ss = float(np.sum((y-np.mean(y))**2))
    aic = aicc = None
    likelihood_parameters = parameter_count if p.absolute_sigma else parameter_count+1
    if objective > 0:
        neg2ll = objective + float(np.sum(np.log(2*math.pi*sigma**2))) if p.absolute_sigma else (
            n*(math.log(2*math.pi)+1+math.log(objective/n))+2*float(np.log(sigma).sum()))
        aic = neg2ll + 2*likelihood_parameters
        if n > likelihood_parameters+1:
            aicc = aic+2*likelihood_parameters*(likelihood_parameters+1)/(n-likelihood_parameters-1)
    replicates = {}
    for xv, yv in zip(x, y):
        replicates.setdefault(float(xv), []).append(float(yv))
    pure_error = sum(sum((v-np.mean(values))**2 for v in values) for values in replicates.values())
    lack_of_fit = None
    if p.sigma_y is None and len(replicates) < n and len(replicates) > parameter_count and pure_error > 0:
        df_pure, df_lack = n-len(replicates), len(replicates)-parameter_count
        f_value = max(ss-pure_error, 0)/df_lack/(pure_error/df_pure)
        lack_of_fit = {"F": f_value, "p_value": float(f_distribution.sf(f_value, df_lack, df_pure)), "df_lack_of_fit": df_lack, "df_pure_error": df_pure}
    grid = _prepared_x(p, p.prediction_x) if p.prediction_x else np.linspace(float(min(x)), float(max(x)), 100)
    grid_values, grid_jac = model_values(p.model, grid, theta, reference)
    spread = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", grid_jac, covariance, grid_jac), 0))*quantile if covariance is not None and not active_bounds else None
    if p.model == "arrhenius":
        declared_grid = _units.Quantity(grid, "K").to(p.x_unit).magnitude
    else:
        declared_grid = grid
    if np.any(grid < min(x)) or np.any(grid > max(x)):
        warnings.append("包含显式请求的范围外预测；置信区间不计入外推造成的模型误差。")
    residual_scale = 1.0 if p.absolute_sigma else math.sqrt(objective/df)
    result = {
        "model": p.model, "equation": MODELS[p.model], "reference_temperature_K": reference,
        "parameters": parameters, "confidence": p.confidence,
        "uncertainty_method": "known_sigma_normal_local" if p.absolute_sigma else "estimated_scale_student_t_local",
        "parameter_covariance": covariance.tolist() if covariance is not None else None,
        "metrics": {"observations": n, "degrees_of_freedom": df, "SSE": ss, "weighted_SSE": objective,
                    "RMSE": math.sqrt(ss/n), "residual_standard_error": math.sqrt(ss/df),
                    "R_squared": 1-ss/total_ss if total_ss > 0 else None, "AIC": aic, "AICc": aicc},
        "diagnostics": {"jacobian_rank": rank, "scaled_jacobian_condition": float(singular[0]/singular[-1]) if singular[-1] > 0 else None,
                        "parameter_correlation": correlation, "active_parameter_bounds": active_bounds, "lack_of_fit": lack_of_fit},
        "observations": [{"x": float(a), "y": float(b), "predicted": float(c), "residual": float(d), "standardized_residual": float(d/s/residual_scale) if residual_scale > 0 else None} for a, b, c, d, s in zip(p.x, y, predicted, residual, sigma)],
        "curve": [{"x": float(a), "y": float(b), "mean_confidence_interval": [float(b-spread[i]), float(b+spread[i])] if spread is not None else None,
                   "extrapolated": bool(grid[i] < min(x) or grid[i] > max(x))} for i, (a, b) in enumerate(zip(declared_grid, grid_values))],
        "x_unit": p.x_unit, "y_unit": p.y_unit,
        "validation": {"numerical_fit_converged": True, "physical_model_validated": False},
        "warnings": warnings,
    }
    return result
