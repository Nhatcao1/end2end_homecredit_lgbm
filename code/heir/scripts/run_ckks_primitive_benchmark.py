#!/usr/bin/env python3
"""Execute the generated CKKS primitive suite and write a compact report.

This runner is intentionally separate from credit workloads. It runs CT+CT,
CT-CT, and CT×CT sequentially over deterministic generated CSV pairs and
compares their latency/accuracy with a Python numeric baseline.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json
from code.heir.scripts.run_payment_features_ciphertext_demo import copy_generated_sources, run


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(ckks_primitive_benchmark LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(primitive_runner add_output.cpp sub_output.cpp mul_output.cpp primitive_runner.cpp)
target_include_directories(primitive_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(primitive_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(primitive_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(primitive_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''

RUNNER = r'''
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "add_output.h"
#include "sub_output.h"
#include "mul_output.h"
using namespace lbcrypto;

struct Pair { std::vector<double> left, right; };
double sec(std::chrono::steady_clock::time_point start) { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); }
void need(bool ok, const std::string& message) { if (!ok) throw std::runtime_error(message); }
Pair readPair(const std::string& path) {
  std::ifstream input(path); need(input.good(), "cannot open " + path);
  std::string line; std::getline(input, line); Pair p;
  while (std::getline(input, line)) { if (line.empty()) continue; std::stringstream s(line); std::string a,b; std::getline(s,a,','); std::getline(s,b,','); p.left.push_back(std::stod(a)); p.right.push_back(std::stod(b)); }
  need(p.left.size() == p.right.size() && !p.left.empty(), "invalid pair file"); return p;
}
std::vector<double> chunk(const std::vector<double>& values, size_t start, size_t slots) {
  std::vector<double> out(slots, 0.0); size_t take = std::min(slots, values.size() - start); std::copy(values.begin()+start, values.begin()+start+take, out.begin()); return out;
}
template <class F>
void audit(const std::vector<double>& decoded, const Pair& pair, size_t start, F expected, double& total, double& maximum, uint64_t& count) {
  size_t take = std::min(decoded.size(), pair.left.size() - start);
  for (size_t i=0; i<take; ++i) { double error = std::abs(decoded[i] - expected(pair.left[start+i], pair.right[start+i])); total += error; maximum = std::max(maximum,error); ++count; }
}
int main(int argc, char** argv) {
  if (argc != 5) return 2;
  try {
    const std::string dataDir(argv[1]), resultPath(argv[2]), executionPath(argv[4]); const int repetitions = std::stoi(argv[3]); const size_t slots = @SLOTS@;
    auto setupStarted=std::chrono::steady_clock::now();
    auto context = encrypted_multiply__generate_crypto_context(); auto keys = context->KeyGen(); need(keys.good(), "key generation failed");
    context = encrypted_multiply__configure_crypto_context(context, keys.secretKey);
    context = encrypted_add__configure_crypto_context(context, keys.secretKey);
    context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    const double setupSeconds=sec(setupStarted);
    std::ofstream execution(executionPath); execution << std::setprecision(17) << "{\"setup_seconds\":" << setupSeconds << ",\"ring_dimension\":" << context->GetRingDimension() << ",\"requested_slot_count\":" << slots << ",\"omp_num_threads\":1}\n";
    std::ofstream out(resultPath); out << "calculation,value_count,decimals,repetition,ciphertext_chunks,encrypt_seconds,evaluate_seconds,decrypt_seconds,online_seconds,mae,max_abs_error\n" << std::setprecision(17);
    for (const auto& op : {std::string("CT+CT"), std::string("CT-CT"), std::string("CTxCT")}) {
      const std::string dataset = op == "CTxCT" ? "multiply" : "add_sub";
      for (size_t count : {size_t(1000), size_t(50000), size_t(1000000)}) for (int decimals : {1,2,3,6}) {
        Pair pair = readPair(dataDir + "/" + dataset + "_" + std::to_string(count) + "_" + std::to_string(decimals) + "dp.csv");
        for (int repeat=0; repeat<repetitions; ++repeat) {
          double encrypt=0, evaluate=0, decrypt=0, total=0, maximum=0; uint64_t audited=0; size_t chunks=0;
          for (size_t start=0; start<pair.left.size(); start += slots, ++chunks) {
            auto left = chunk(pair.left,start,slots), right = chunk(pair.right,start,slots); auto began=std::chrono::steady_clock::now();
            if (op == "CT+CT") { auto a=encrypted_add__encrypt__arg0(context,left,keys.publicKey); auto b=encrypted_add__encrypt__arg1(context,right,keys.publicKey); encrypt += sec(began); began=std::chrono::steady_clock::now(); auto r=encrypted_add(context,a,b); evaluate += sec(began); began=std::chrono::steady_clock::now(); auto d=encrypted_add__decrypt__result0(context,r,keys.secretKey); decrypt += sec(began); audit(d,pair,start,[](double x,double y){return x+y;},total,maximum,audited); }
            else if (op == "CT-CT") { auto a=encrypted_subtract__encrypt__arg0(context,left,keys.publicKey); auto b=encrypted_subtract__encrypt__arg1(context,right,keys.publicKey); encrypt += sec(began); began=std::chrono::steady_clock::now(); auto r=encrypted_subtract(context,a,b); evaluate += sec(began); began=std::chrono::steady_clock::now(); auto d=encrypted_subtract__decrypt__result0(context,r,keys.secretKey); decrypt += sec(began); audit(d,pair,start,[](double x,double y){return x-y;},total,maximum,audited); }
            else { auto a=encrypted_multiply__encrypt__arg0(context,left,keys.publicKey); auto b=encrypted_multiply__encrypt__arg1(context,right,keys.publicKey); encrypt += sec(began); began=std::chrono::steady_clock::now(); auto r=encrypted_multiply(context,a,b); evaluate += sec(began); began=std::chrono::steady_clock::now(); auto d=encrypted_multiply__decrypt__result0(context,r,keys.secretKey); decrypt += sec(began); audit(d,pair,start,[](double x,double y){return x*y;},total,maximum,audited); }
          }
          out << op << ',' << count << ',' << decimals << ',' << repeat+1 << ',' << chunks << ',' << encrypt << ',' << evaluate << ',' << decrypt << ',' << encrypt+evaluate+decrypt << ',' << total/audited << ',' << maximum << '\n';
        }
      }
    }
    return 0;
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
'''


def python_baseline(data_dir: Path, output: Path, repetitions: int) -> None:
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["calculation", "value_count", "decimals", "repetition", "python_seconds"])
        for calculation, dataset, fn in (("CT+CT", "add_sub", lambda a,b:a+b), ("CT-CT", "add_sub", lambda a,b:a-b), ("CTxCT", "multiply", lambda a,b:a*b)):
            for count in (1000, 50000, 1000000):
                for decimals in (1,2,3,6):
                    with (data_dir / f"{dataset}_{count}_{decimals}dp.csv").open(newline="", encoding="utf-8") as source:
                        rows = [(float(row["left"]), float(row["right"])) for row in csv.DictReader(source)]
                    for repeat in range(1, repetitions + 1):
                        started = time.perf_counter(); _ = [fn(a,b) for a,b in rows]
                        writer.writerow([calculation, count, decimals, repeat, time.perf_counter() - started])


def report(he_csv: Path, python_csv: Path, execution_json: Path, output: Path) -> None:
    def grouped(path: Path, field: str) -> dict[tuple[str,str,str], list[float]]:
        result: dict[tuple[str,str,str], list[float]] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle): result.setdefault((row["calculation"],row["value_count"],row["decimals"]), []).append(float(row[field]))
        return result
    he = grouped(he_csv, "evaluate_seconds"); py = grouped(python_csv, "python_seconds")
    encryption = grouped(he_csv, "encrypt_seconds"); decrypt = grouped(he_csv, "decrypt_seconds"); online = grouped(he_csv, "online_seconds")
    execution = json.loads(execution_json.read_text(encoding="utf-8"))
    depth = execution.get("multiply_context_budget", {})
    depth_note = f" Multiply context depth: `{depth.get('requested_multiplicative_depth')}` (translated default: `{depth.get('translated_depth_before_patch')}`)." if depth else ""
    lines = ["# CKKS primitive benchmark", "", f"One CKKS context/key set setup: `{execution['setup_seconds']:.9f}` s. Runtime ring dimension: `{execution['ring_dimension']}`. Requested slots: `{execution['requested_slot_count']}`. `OMP_NUM_THREADS=1`. Setup is a shared one-time cost and is excluded from the operation slowdown columns.{depth_note}", "", "| Calculation | Values | Decimals | Python calculation (median s) | HE evaluation (median s) | HE encryption (median s) | HE decryption (median s) | HE online: encrypt + evaluate + decrypt (median s) | Eval ÷ Python | Online ÷ Python |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key in sorted(he):
        label = "CT×CT" if key[0] == "CTxCT" else key[0]
        python_seconds = statistics.median(py[key]); evaluation_seconds = statistics.median(he[key]); online_seconds = statistics.median(online[key])
        lines.append(f"| {label} | {key[1]} | {key[2]} | {python_seconds:.9f} | {evaluation_seconds:.9f} | {statistics.median(encryption[key]):.9f} | {statistics.median(decrypt[key]):.9f} | {online_seconds:.9f} | {evaluation_seconds / python_seconds:.2f}× | {online_seconds / python_seconds:.2f}× |")
    with he_csv.open(newline="", encoding="utf-8") as handle:
        errors = [float(row["max_abs_error"]) for row in csv.DictReader(handle)]
    lines += ["", "`Eval ÷ Python` compares only the encrypted arithmetic with the Python arithmetic. `Online ÷ Python` includes encryption and audit decryption. The shared setup cost is reported above but not amortized into either ratio.", "", f"Acceptance: max absolute error ≤ 1e-6. Observed maximum: `{max(errors):.12g}`.", "", "Raw timing and accuracy rows: `heir_results.csv`, `python_results.csv`."]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--ckks-mul-depth", type=int, default=3, help="multiply-capable CKKS depth; default: 3")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(); root=args.output_dir.resolve()
    if root.exists():
        if not args.overwrite: raise FileExistsError(f"refusing to overwrite: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    generated=args.generated_dir.resolve(); work=root/"runner"; work.mkdir()
    for directory,prefix in ((generated/"00_encrypted_add","add"),(generated/"01_encrypted_subtract","sub"),(generated/"02_encrypted_multiply","mul")): copy_generated_sources(directory,work,prefix)
    if args.ckks_mul_depth < 2: raise ValueError("--ckks-mul-depth must be at least 2")
    multiply_cpp = work / "mul_output.cpp"
    pattern = r"(SetMultiplicativeDepth\()\d+(\s*\);)"
    match = re.search(pattern, multiply_cpp.read_text(encoding="utf-8"))
    if match is None: raise ValueError("generated multiply source has no SetMultiplicativeDepth call")
    original_depth = int(re.search(r"\d+", match.group(0)).group(0))
    patched, replacements = re.subn(pattern, rf"\g<1>{args.ckks_mul_depth}\g<2>", multiply_cpp.read_text(encoding="utf-8"))
    if replacements != 1: raise ValueError(f"expected one multiply depth setting; found {replacements}")
    multiply_cpp.write_text(patched, encoding="utf-8")
    (work/"primitive_runner.cpp").write_text(RUNNER.replace("@SLOTS@", "8192"),encoding="utf-8"); (work/"CMakeLists.txt").write_text(CMAKE,encoding="utf-8")
    build=work/"build"; configure,_=run(["cmake","-S",str(work.resolve()),"-B",str(build.resolve()),f"-DOpenFHE_DIR={args.openfhe_dir}"],work); build_seconds,_=run(["cmake","--build",str(build.resolve()),"--target","primitive_runner"],work)
    py=root/"python_results.csv"; python_baseline(args.data_dir.resolve(),py,args.repetitions)
    he=root/"heir_results.csv"; execution=root/"execution.json"; wall,log=run(["env","OMP_NUM_THREADS=1",str((build/"primitive_runner").resolve()),str(args.data_dir.resolve()),str(he.resolve()),str(args.repetitions),str(execution.resolve())],work); (work/"runner.log").write_text(log,encoding="utf-8")
    execution_data=json.loads(execution.read_text(encoding="utf-8")); execution_data["multiply_context_budget"]={"translated_depth_before_patch":original_depth,"requested_multiplicative_depth":args.ckks_mul_depth}; write_json(execution, execution_data)
    report(he,py,execution,root/"REPORT.md"); result={"status":"ckks_primitive_benchmark_executed","report":"REPORT.md","execution":"execution.json","build_seconds":{"configure":configure,"build":build_seconds},"runner_wall_seconds":wall}; write_json(root/"result.json",result); print(json.dumps(result,indent=2))


if __name__ == "__main__": main()
