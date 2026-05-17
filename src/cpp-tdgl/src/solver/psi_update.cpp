#include "solver/psi_update.h"
#include <cmath>
#include <complex>

using std::complex;

std::optional<PsiResult> solve_for_psi_squared(
    const Eigen::VectorXcd& psi, const Eigen::VectorXd& abs_sq_psi,
    const Eigen::VectorXd& mu, const Eigen::VectorXd& epsilon,
    double gamma, double u, double dt,
    const Eigen::SparseMatrix<complex<double>>& psi_laplacian) {

    int n = psi.size();

    // U = exp(-i * mu * dt)
    Eigen::VectorXcd U = (complex<double>(0, -1) * mu.array() * dt).exp();

    // z = U * (gamma^2 / 2) * psi
    Eigen::VectorXcd z = U.array() * (gamma * gamma / 2.0) * psi.array();

    // psi_laplacian @ psi
    Eigen::VectorXcd psi_lap = psi_laplacian * psi;

    // sqrt(1 + gamma^2 * |psi|^2)
    Eigen::VectorXd sqrt_factor = (1.0 + gamma * gamma * abs_sq_psi.array()).sqrt();

    // w = z * |psi|^2 + U * (psi + (dt/u) * sqrt(1+gamma^2*|psi|^2) * ((epsilon - |psi|^2) * psi + psi_laplacian))
    Eigen::VectorXcd w = z.array() * abs_sq_psi.array()
        + U.array() * (psi.array()
            + (dt / u) * sqrt_factor.array()
                * ((epsilon.array() - abs_sq_psi.array()) * psi.array() + psi_lap.array()));

    // c = Re(w)*Re(z) + Im(w)*Im(z)
    Eigen::VectorXd c = w.real().cwiseProduct(z.real()) + w.imag().cwiseProduct(z.imag());

    Eigen::VectorXd two_c_1 = 2.0 * c.array() + 1.0;
    Eigen::VectorXd w2 = w.array().abs().square();
    Eigen::VectorXd z2 = z.array().abs().square();

    // discriminant = (2c+1)^2 - 4*|z|^2*|w|^2
    Eigen::VectorXd discriminant = two_c_1.array().square() - 4.0 * z2.array() * w2.array();

    // Check for negative discriminant (unphysical)
    if ((discriminant.array() < 0.0).any()) {
        return std::nullopt;
    }

    // |psi_new|^2 = 2*|w|^2 / ((2c+1) + sqrt(discriminant))
    Eigen::VectorXd new_sq_psi = 2.0 * w2.array()
        / (two_c_1.array() + discriminant.array().sqrt());

    // psi_new = w - z * |psi_new|^2 (z is complex, new_sq_psi is real)
    Eigen::VectorXcd psi_new = w.array() - z.array() * new_sq_psi.array();

    return PsiResult{psi_new, new_sq_psi};
}
