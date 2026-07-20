"""Generic encrypted sum used after a feature ciphertext is produced."""

from __future__ import annotations


def encrypted_sum_mlir(vector_size: int) -> str:
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    tensor = f"tensor<{vector_size}xf64>"
    lines = [
        "func.func @encrypted_sum(",
        f"    %values: {tensor} {{secret.secret}}",
        ") -> f64 {",
    ]

    # Seed the reduction from encrypted tensor elements, rather than a
    # plaintext 0.0 accumulator. This keeps the operation level-preserving
    # when it consumes the output of an earlier, deeper CKKS circuit.
    current = []
    for index in range(vector_size):
        name = f"%value_{index}"
        lines.append(f"  {name} = tensor.extract %values[{index}] : {tensor}")
        current.append(name)

    add_index = 0
    while len(current) > 1:
        next_level = []
        for index in range(0, len(current), 2):
            if index + 1 == len(current):
                next_level.append(current[index])
                continue
            result = f"%sum_{add_index}"
            lines.append(
                f"  {result} = arith.addf {current[index]}, "
                f"{current[index + 1]} : f64"
            )
            next_level.append(result)
            add_index += 1
        current = next_level

    lines.extend([f"  return {current[0]} : f64", "}"])
    return "\n".join(lines) + "\n"
