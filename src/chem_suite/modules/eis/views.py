"""Scientific plot data generated in workers, independently of Qt."""

import numpy as np
from chem_suite.core.plots import plot, series


def spectrum_plots(frequency, measured, predicted=None, kk=None, *, limit=2000):
    def curve(label, x, y, kind="line"):
        return series(label, x, y, kind=kind, limit=max(2, limit))

    def comparisons(x, values, fitted):
        curves = [curve("Measured", x, values, "scatter")]
        if fitted is not None:
            curves.append(curve("Model", x, fitted))
        return curves

    nyquist = [curve("Measured", measured.real, -measured.imag, "scatter")]
    if predicted is not None:
        nyquist.append(curve("Model", predicted.real, -predicted.imag))
    plots = [
        plot("Nyquist", "Re(Z), Ω", "−Im(Z), Ω", nyquist, equal_aspect=True),
        plot("Bode magnitude", "Frequency, Hz", "|Z|, Ω", comparisons(
            frequency, np.abs(measured), np.abs(predicted) if predicted is not None else None,
        ), xscale="log", yscale="log"),
        plot("Bode phase", "Frequency, Hz", "Phase, °", comparisons(
            frequency, np.angle(measured, deg=True), np.angle(predicted, deg=True) if predicted is not None else None,
        ), xscale="log"),
    ]
    if predicted is not None:
        residual = measured - predicted
        relative = residual / np.maximum(np.abs(measured), 1e-30) * 100
        plots.extend([
            plot("Fit residuals", "Frequency, Hz", "Residual, Ω", [
                curve("Real component", frequency, residual.real),
                curve("Imaginary component", frequency, residual.imag),
            ], xscale="log"),
            plot("Relative residuals", "Frequency, Hz", "Residual, %", [
                curve("Real component", frequency, relative.real),
                curve("Imaginary component", frequency, relative.imag),
                curve("Magnitude", frequency, np.abs(relative)),
            ], xscale="log"),
        ])
    if kk is not None and kk.success and kk.z_fit is not None:
        order = np.argsort(frequency)
        unique, indices = np.unique(frequency[order], return_index=True)
        observed = measured[order][indices]
        if not np.array_equal(unique, kk.frequencies):
            raise ValueError("KK frequency alignment differs from the measured spectrum")
        plots.extend([
            plot("KK Nyquist", "Re(Z), Ω", "−Im(Z), Ω", [
                curve("Measured", observed.real, -observed.imag, "scatter"),
                curve("Lin-KK", kk.z_fit.real, -kk.z_fit.imag),
            ], equal_aspect=True),
            plot("KK residuals", "Frequency, Hz", "Residual, %", [
                curve("Real component", kk.frequencies, kk.residual_real * 100),
                curve("Imaginary component", kk.frequencies, kk.residual_imag * 100),
            ], xscale="log"),
        ])
    return plots


def save_spectrum(path, dataset, kk, predicted=None):
    arrays = {"frequency_hz": dataset.frequencies, "z_real_ohm": dataset.z.real, "z_imag_ohm": dataset.z.imag}
    if predicted is not None:
        arrays.update(fit_real_ohm=predicted.real, fit_imag_ohm=predicted.imag)
    if kk.success:
        arrays.update(
            kk_frequency_hz=kk.frequencies, kk_real_ohm=kk.z_fit.real, kk_imag_ohm=kk.z_fit.imag,
            kk_residual_real=kk.residual_real, kk_residual_imag=kk.residual_imag,
        )
    np.savez(path, **arrays)
