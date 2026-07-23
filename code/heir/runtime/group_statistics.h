#pragma once

// Reusable OpenFHE/HEIR composition layer for a fixed client-prepared group.
//
// HEIR generates the primitive CKKS functions (subtract, multiply and sum).
// This header deliberately does not mention a business feature such as
// PAYMENT_DIFF. A benchmark supplies its encrypted parent vectors and the
// generated functions as callbacks.

#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "openfhe.h"

namespace heir::runtime {

using Context = lbcrypto::CryptoContext<lbcrypto::DCRTPoly>;
using Ciphertext = lbcrypto::Ciphertext<lbcrypto::DCRTPoly>;
using Bundle = std::vector<Ciphertext>;

struct OpaqueGroupBlock {
  std::uint64_t opaque_group_id;
  std::size_t public_valid_count;
  std::vector<double> left_for_moments;
  std::vector<double> right_for_moments;
  std::vector<double> left_for_max;
  std::vector<double> right_for_max;
};

struct GroupStatisticsTiming {
  double encrypt_seconds = 0.0;
  double feature_seconds = 0.0;
  double square_seconds = 0.0;
  double sum_reduce_seconds = 0.0;
  double variance_finalize_seconds = 0.0;
  double max_seconds = 0.0;
};

struct EncryptedGroupStatistics {
  std::uint64_t opaque_group_id;
  std::size_t public_valid_count;
  GroupStatisticsTiming timing;
  Bundle sum;
  Bundle mean;
  Bundle sample_variance;
  Ciphertext maximum;
};

inline double elapsed_seconds(std::chrono::steady_clock::time_point started) {
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - started)
      .count();
}

inline void require_scalar_bundle(const Bundle& values, const char* operation) {
  if (values.size() != 1)
    throw std::runtime_error(std::string(operation) + " must return one encrypted scalar");
}

// Generic CKKS mean where the group count is intentionally public metadata.
inline Bundle mean_from_sum(const Context& context, const Bundle& sum,
                            std::size_t public_valid_count) {
  require_scalar_bundle(sum, "sum");
  if (public_valid_count == 0)
    throw std::runtime_error("mean requires a positive public valid count");
  return Bundle{context->EvalMult(sum[0], 1.0 / static_cast<double>(public_valid_count))};
}

// Generic ddof=1 sample variance from encrypted SUM(x) and SUM(x²):
// (SUM(x²) - SUM(x) * MEAN(x)) / (n - 1).
inline Bundle sample_variance_from_moments(const Context& context,
                                           const Bundle& sum,
                                           const Bundle& sum_squares,
                                           const Bundle& mean,
                                           std::size_t public_valid_count) {
  require_scalar_bundle(sum, "sum");
  require_scalar_bundle(sum_squares, "sum_squares");
  require_scalar_bundle(mean, "mean");
  if (public_valid_count < 2)
    throw std::runtime_error("sample variance requires public valid count >= 2");
  auto centered_sum_squares = context->EvalSub(
      sum_squares[0], context->EvalMult(sum[0], mean[0]));
  return Bundle{context->EvalMult(
      centered_sum_squares, 1.0 / static_cast<double>(public_valid_count - 1))};
}

// Run a reusable encrypted feature -> moments -> aggregate loop. The caller
// supplies HEIR-generated encryption/subtract/multiply/sum functions and an
// optional scheme-switch MAX function. No decrypt callback exists here by
// design: audit belongs outside this reusable encrypted layer.
template <class EncryptLeft, class EncryptRight, class Subtract,
          class Multiply, class Sum, class Maximum>
std::vector<EncryptedGroupStatistics> evaluate_group_statistics(
    const Context& context,
    const lbcrypto::PublicKey<lbcrypto::DCRTPoly>& public_key,
    const std::vector<OpaqueGroupBlock>& groups, EncryptLeft encrypt_left,
    EncryptRight encrypt_right, Subtract subtract, Multiply multiply, Sum sum,
    Maximum maximum) {
  std::vector<EncryptedGroupStatistics> result;
  result.reserve(groups.size());
  for (const auto& group : groups) {
    if (group.public_valid_count < 2)
      throw std::runtime_error("group executor requires count >= 2");
    auto started = std::chrono::steady_clock::now();
    auto left = encrypt_left(context, group.left_for_moments, public_key);
    auto right = encrypt_right(context, group.right_for_moments, public_key);
    auto max_left = encrypt_left(context, group.left_for_max, public_key);
    auto max_right = encrypt_right(context, group.right_for_max, public_key);
    GroupStatisticsTiming timing;
    timing.encrypt_seconds = elapsed_seconds(started);

    started = std::chrono::steady_clock::now();
    auto feature = subtract(context, left, right);
    auto max_feature = subtract(context, max_left, max_right);
    timing.feature_seconds = elapsed_seconds(started);

    auto square_input = feature;
    started = std::chrono::steady_clock::now();
    auto squared = multiply(context, square_input, square_input);
    timing.square_seconds = elapsed_seconds(started);

    auto sum_input = feature;
    auto square_sum_input = squared;
    started = std::chrono::steady_clock::now();
    auto encrypted_sum = sum(context, sum_input);
    auto encrypted_sum_squares = sum(context, square_sum_input);
    require_scalar_bundle(encrypted_sum, "sum kernel");
    require_scalar_bundle(encrypted_sum_squares, "sum-squares kernel");
    timing.sum_reduce_seconds = elapsed_seconds(started);

    started = std::chrono::steady_clock::now();
    auto encrypted_mean = mean_from_sum(context, encrypted_sum, group.public_valid_count);
    auto encrypted_variance = sample_variance_from_moments(
        context, encrypted_sum, encrypted_sum_squares, encrypted_mean,
        group.public_valid_count);
    timing.variance_finalize_seconds = elapsed_seconds(started);

    started = std::chrono::steady_clock::now();
    auto encrypted_maximum = maximum(context, max_feature);
    timing.max_seconds = elapsed_seconds(started);

    result.push_back(EncryptedGroupStatistics{
        group.opaque_group_id, group.public_valid_count, timing,
        std::move(encrypted_sum), std::move(encrypted_mean),
        std::move(encrypted_variance), std::move(encrypted_maximum)});
  }
  return result;
}

}  // namespace heir::runtime
