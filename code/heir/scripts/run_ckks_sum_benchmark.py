#!/usr/bin/env python3
"""Run the isolated CKKS-SUM-01 benchmark over one padded input vector."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code.heir.common import write_json
from code.heir.scripts.run_payment_features_ciphertext_demo import copy_generated_sources, run

CMAKE=r'''cmake_minimum_required(VERSION 3.16)
project(ckks_sum_benchmark LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(sum_runner sum_output.cpp sum_runner.cpp)
target_include_directories(sum_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(sum_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(sum_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(sum_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''
RUNNER=r'''
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "sum_output.h"
using namespace lbcrypto;
double seconds(std::chrono::steady_clock::time_point s){return std::chrono::duration<double>(std::chrono::steady_clock::now()-s).count();}
std::vector<double> read(const std::string& path){std::ifstream in(path);if(!in)throw std::runtime_error("cannot open "+path);std::string line;std::getline(in,line);std::vector<double> v;while(std::getline(in,line)){std::stringstream s(line);std::string x;std::getline(s,x,',');v.push_back(std::stod(x));}return v;}
int main(int argc,char**argv){if(argc!=5)return 2;try{auto setup=std::chrono::steady_clock::now();auto ctx=encrypted_sum__generate_crypto_context();auto keys=ctx->KeyGen();if(!keys.good())throw std::runtime_error("key generation failed");ctx=encrypted_sum__configure_crypto_context(ctx,keys.secretKey);double setup_s=seconds(setup);std::ofstream out(argv[3]);out<<"decimals,repetition,encrypt_seconds,evaluate_seconds,decrypt_seconds,online_seconds,he_sum,abs_error\n"<<std::setprecision(17);std::ofstream meta(argv[4]);meta<<"{\"setup_seconds\":"<<setup_s<<",\"ring_dimension\":"<<ctx->GetRingDimension()<<",\"omp_num_threads\":1}\n";for(int d:{1,2,3,6}){auto v=read(std::string(argv[1])+"/add_sub_"+argv[2]+"_"+std::to_string(d)+"dp.csv");if(v.size()>@SIZE@)throw std::runtime_error("input exceeds generated sum size");double plain=0;for(double x:v)plain+=x;v.resize(@SIZE@,0.0);for(int r=1;r<=5;++r){auto t=std::chrono::steady_clock::now();auto a=encrypted_sum__encrypt__arg0(ctx,v,keys.publicKey);double enc=seconds(t);t=std::chrono::steady_clock::now();auto result=encrypted_sum(ctx,a);double eval=seconds(t);t=std::chrono::steady_clock::now();double value=encrypted_sum__decrypt__result0(ctx,result,keys.secretKey);double dec=seconds(t);out<<d<<','<<r<<','<<enc<<','<<eval<<','<<dec<<','<<enc+eval+dec<<','<<value<<','<<std::abs(value-plain)<<'\n';}}return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
'''

def pandas_sum(data: Path, count: int, output: Path) -> None:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError("CKKS-SUM-01 requires pandas for its plaintext reference; install it with `python3 -m pip install pandas`") from error
    with output.open("w",newline="",encoding="utf-8") as h:
        w=csv.writer(h);w.writerow(["decimals","repetition","pandas_seconds"])
        for d in (1,2,3,6):
            values=pd.read_csv(data/f"add_sub_{count}_{d}dp.csv",usecols=["left"])["left"]
            for r in range(1,6): t=time.perf_counter();values.sum();w.writerow([d,r,time.perf_counter()-t])

def main() -> None:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--generated-dir",type=Path,required=True);p.add_argument("--data-dir",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--value-count",type=int,default=1000);p.add_argument("--openfhe-dir",default="/usr/local/lib/OpenFHE");p.add_argument("--overwrite",action="store_true");a=p.parse_args();root=a.output_dir.resolve()
    if root.exists():
        if not a.overwrite: raise FileExistsError(f"refusing to overwrite: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True);manifest=json.loads((a.generated_dir/"generation_manifest.json").read_text());kernel=next(k for k in manifest["kernels"] if k["entry_function"]=="encrypted_sum");size=int(kernel["logical_value_count"]);source=(a.generated_dir/kernel["source"]).parent;work=root/"runner";work.mkdir();copy_generated_sources(source,work,"sum");(work/"sum_runner.cpp").write_text(RUNNER.replace("@SIZE@",str(size)),encoding="utf-8");(work/"CMakeLists.txt").write_text(CMAKE,encoding="utf-8");build=work/"build";run(["cmake","-S",str(work.resolve()),"-B",str(build.resolve()),f"-DOpenFHE_DIR={a.openfhe_dir}"],work);run(["cmake","--build",str(build.resolve()),"--target","sum_runner"],work);py=root/"pandas_results.csv";pandas_sum(a.data_dir.resolve(),a.value_count,py);he=root/"heir_results.csv";meta=root/"execution.json";wall,log=run(["env","OMP_NUM_THREADS=1",str((build/"sum_runner").resolve()),str(a.data_dir.resolve()),str(a.value_count),str(he.resolve()),str(meta.resolve())],work);(work/"runner.log").write_text(log,encoding="utf-8");
    with he.open(newline="",encoding="utf-8") as h: rows=list(csv.DictReader(h))
    with py.open(newline="",encoding="utf-8") as h: prow=list(csv.DictReader(h))
    lines=["# CKKS-SUM-01", "", "Pandas CSV-read/setup time is excluded from the plaintext calculation timing, just as HE setup is reported separately.", "", "| Decimals | Pandas sum median (s) | HE evaluation median (s) | HE encrypt median (s) | HE decrypt median (s) | HE online median (s) | Max absolute error |", "|---:|---:|---:|---:|---:|---:|---:|"]
    for d in ("1","2","3","6"):
        hr=[r for r in rows if r["decimals"]==d];pr=[r for r in prow if r["decimals"]==d];lines.append(f"| {d} | {statistics.median(float(r['pandas_seconds']) for r in pr):.9f} | {statistics.median(float(r['evaluate_seconds']) for r in hr):.9f} | {statistics.median(float(r['encrypt_seconds']) for r in hr):.9f} | {statistics.median(float(r['decrypt_seconds']) for r in hr):.9f} | {statistics.median(float(r['online_seconds']) for r in hr):.9f} | {max(float(r['abs_error']) for r in hr):.12g} |")
    (root/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8");result={"status":"ckks_sum_benchmark_executed","report":"REPORT.md","runner_wall_seconds":wall};write_json(root/"result.json",result);print(json.dumps(result,indent=2))
if __name__=="__main__":main()
