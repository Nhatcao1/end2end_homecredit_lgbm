#!/usr/bin/env python3
"""Run the shared CKKS-SUM-01 and CKKS-MEAN-01 benchmark."""
from __future__ import annotations
import argparse, csv, json, shutil, statistics, sys, time
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
add_executable(sum_runner sum_output.cpp mean_output.cpp sum_runner.cpp)
target_include_directories(sum_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(sum_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(sum_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(sum_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''
RUNNER=r'''
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
#include "mean_output.h"
#include "sum_output.h"
using namespace lbcrypto;
double seconds(std::chrono::steady_clock::time_point s){return std::chrono::duration<double>(std::chrono::steady_clock::now()-s).count();}
std::vector<double> read(const std::string& path){std::ifstream in(path);if(!in)throw std::runtime_error("cannot open "+path);std::string line;std::getline(in,line);std::vector<double> v;while(std::getline(in,line)){std::stringstream s(line);std::string x;std::getline(s,x,',');v.push_back(std::stod(x));}return v;}
std::vector<size_t> counts(const std::string& text){std::vector<size_t> out;std::stringstream s(text);std::string x;while(std::getline(s,x,','))out.push_back(std::stoull(x));return out;}
int main(int argc,char**argv){if(argc!=5)return 2;try{const size_t slots=@SIZE@;auto setup=std::chrono::steady_clock::now();auto ctx=fixed_count_mean__generate_crypto_context();auto keys=ctx->KeyGen();if(!keys.good())throw std::runtime_error("key generation failed");ctx=fixed_count_mean__configure_crypto_context(ctx,keys.secretKey);ctx=encrypted_sum__configure_crypto_context(ctx,keys.secretKey);std::ofstream meta(argv[4]);meta<<std::setprecision(17)<<"{\"setup_seconds\":"<<seconds(setup)<<",\"ring_dimension\":"<<ctx->GetRingDimension()<<",\"slot_count\":"<<slots<<",\"omp_num_threads\":1}\n";std::ofstream out(argv[3]);out<<"value_count,decimals,repetition,ciphertext_chunks,encrypt_seconds,sum_evaluate_seconds,merge_seconds,mean_scale_seconds,sum_decrypt_seconds,mean_decrypt_seconds,online_seconds,he_sum,sum_abs_error,he_mean,mean_abs_error\n"<<std::setprecision(17);for(size_t count:counts(argv[2]))for(int d:{1,2,3,6}){auto values=read(std::string(argv[1])+"/add_sub_"+std::to_string(count)+"_"+std::to_string(d)+"dp.csv");double plain=0;for(double x:values)plain+=x;const double plainMean=plain/static_cast<double>(count);for(int r=1;r<=5;++r){double enc=0,eval=0,merge=0;size_t chunks=0;bool first=true;decltype(encrypted_sum__encrypt__arg0(ctx,std::vector<double>(slots),keys.publicKey)) total;for(size_t start=0;start<values.size();start+=slots,++chunks){std::vector<double> block(slots,0.0);size_t take=std::min(slots,values.size()-start);std::copy(values.begin()+start,values.begin()+start+take,block.begin());auto t=std::chrono::steady_clock::now();auto input=encrypted_sum__encrypt__arg0(ctx,block,keys.publicKey);enc+=seconds(t);t=std::chrono::steady_clock::now();auto partial=encrypted_sum(ctx,input);eval+=seconds(t);t=std::chrono::steady_clock::now();if(first){total=partial;first=false;}else{for(size_t i=0;i<total.size();++i)total[i]=ctx->EvalAdd(total[i],partial[i]);}merge+=seconds(t);}auto t=std::chrono::steady_clock::now();auto mean=total;const double inverse=1.0/static_cast<double>(count);for(auto& value:mean)value=ctx->EvalMult(value,inverse);const double meanScale=seconds(t);t=std::chrono::steady_clock::now();double sumValue=encrypted_sum__decrypt__result0(ctx,total,keys.secretKey);const double sumDecrypt=seconds(t);t=std::chrono::steady_clock::now();double meanValue=encrypted_sum__decrypt__result0(ctx,mean,keys.secretKey);const double meanDecrypt=seconds(t);out<<count<<','<<d<<','<<r<<','<<chunks<<','<<enc<<','<<eval<<','<<merge<<','<<meanScale<<','<<sumDecrypt<<','<<meanDecrypt<<','<<enc+eval+merge+meanScale+sumDecrypt+meanDecrypt<<','<<sumValue<<','<<std::abs(sumValue-plain)<<','<<meanValue<<','<<std::abs(meanValue-plainMean)<<'\n';}}return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
'''

def pandas_sum(data:Path, counts:tuple[int,...], output:Path)->None:
    try: import pandas as pd
    except ImportError as e: raise RuntimeError("install pandas: python3 -m pip install pandas") from e
    with output.open("w",newline="",encoding="utf-8") as h:
        w=csv.writer(h);w.writerow(["value_count","decimals","repetition","pandas_sum_seconds","pandas_mean_seconds"])
        for count in counts:
            for d in (1,2,3,6):
                values=pd.read_csv(data/f"add_sub_{count}_{d}dp.csv",usecols=["left"])["left"]
                for r in range(1,6):
                    t=time.perf_counter(); values.sum(); sum_seconds=time.perf_counter()-t
                    t=time.perf_counter(); values.mean(); mean_seconds=time.perf_counter()-t
                    w.writerow([count,d,r,sum_seconds,mean_seconds])

def main()->None:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--generated-dir",type=Path,required=True);p.add_argument("--data-dir",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--value-counts",nargs="+",type=int,default=[1000,50000,1000000]);p.add_argument("--openfhe-dir",default="/usr/local/lib/OpenFHE");p.add_argument("--overwrite",action="store_true");a=p.parse_args();root=a.output_dir.resolve()
    if root.exists():
        if not a.overwrite: raise FileExistsError(f"refusing to overwrite: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True);manifest=json.loads((a.generated_dir/"generation_manifest.json").read_text());sum_kernel=next(k for k in manifest["kernels"] if k["entry_function"]=="encrypted_sum");mean_kernel=next(k for k in manifest["kernels"] if k["entry_function"]=="fixed_count_mean");size=int(sum_kernel["logical_value_count"]);assert int(mean_kernel["logical_value_count"])==size, "SUM and MEAN must use the same lane count";work=root/"runner";work.mkdir();copy_generated_sources((a.generated_dir/sum_kernel["source"]).parent,work,"sum");copy_generated_sources((a.generated_dir/mean_kernel["source"]).parent,work,"mean");(work/"sum_runner.cpp").write_text(RUNNER.replace("@SIZE@",str(size)),encoding="utf-8");(work/"CMakeLists.txt").write_text(CMAKE,encoding="utf-8");build=work/"build";run(["cmake","-S",str(work.resolve()),"-B",str(build.resolve()),f"-DOpenFHE_DIR={a.openfhe_dir}"],work);run(["cmake","--build",str(build.resolve()),"--target","sum_runner"],work);counts=tuple(a.value_counts);py=root/"pandas_results.csv";pandas_sum(a.data_dir.resolve(),counts,py);he=root/"heir_results.csv";meta=root/"execution.json";wall,log=run(["env","OMP_NUM_THREADS=1",str((build/"sum_runner").resolve()),str(a.data_dir.resolve()),",".join(map(str,counts)),str(he.resolve()),str(meta.resolve())],work);(work/"runner.log").write_text(log,encoding="utf-8")
    with he.open(newline="",encoding="utf-8") as h: rows=list(csv.DictReader(h))
    with py.open(newline="",encoding="utf-8") as h: prow=list(csv.DictReader(h))
    lines=[
        "# CKKS-SUM-01 and CKKS-MEAN-01",
        "",
        "This runner evaluates **two distinct outputs** during every repetition. `CKKS-SUM-01` returns the encrypted global sum. "
        "`CKKS-MEAN-01` starts from that same encrypted SUM ciphertext and applies the public `1/N` factor. "
        "The shared ciphertext preparation and SUM reduction are counted once, never twice.",
        "",
        "Pandas CSV-read/setup time is excluded. HE context/key setup is separately recorded in `execution.json`. "
        "Audit decryption exists only to compare encrypted results with plaintext; it is included in each workload's end-to-end benchmark value.",
        "",
        "## CKKS-SUM-01 — encrypted sum",
        "",
        "The HEIR-generated SUM kernel reduces each 8,192-value ciphertext. `HE merge` adds those encrypted partial sums when the logical input has more than 8,192 values.",
        "",
        "| Values | Decimals | Pandas sum (s) | HE encrypt (s) | HE SUM reduction (s) | HE merge (s) | Audit decrypt (s) | SUM end-to-end online (s) | Sum max error |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for count in counts:
        for d in ("1","2","3","6"):
            hr=[r for r in rows if r["value_count"]==str(count) and r["decimals"]==d];pr=[r for r in prow if r["value_count"]==str(count) and r["decimals"]==d]
            sum_online=(float(r["encrypt_seconds"])+float(r["sum_evaluate_seconds"])+float(r["merge_seconds"])+float(r["sum_decrypt_seconds"]) for r in hr)
            lines.append(f"| {count} | {d} | {statistics.median(float(r['pandas_sum_seconds']) for r in pr):.9f} | {statistics.median(float(r['encrypt_seconds']) for r in hr):.9f} | {statistics.median(float(r['sum_evaluate_seconds']) for r in hr):.9f} | {statistics.median(float(r['merge_seconds']) for r in hr):.9f} | {statistics.median(float(r['sum_decrypt_seconds']) for r in hr):.9f} | {statistics.median(sum_online):.9f} | {max(float(r['sum_abs_error']) for r in hr):.12g} |")
    lines.extend([
        "",
        "## CKKS-MEAN-01 — encrypted mean derived from SUM",
        "",
        "This is not a second SUM benchmark. Its inherited encrypted path is the SUM route above; `HE mean scale` is the **additional** ciphertext × public `1/N` operation. "
        "`MEAN end-to-end online` includes the inherited SUM path once, then that scale and Mean audit decryption.",
        "",
        "| Values | Decimals | Pandas mean (s) | Inherited SUM path (s) | HE mean scale only (s) | Audit decrypt (s) | MEAN end-to-end online (s) | Mean max error |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for count in counts:
        for d in ("1","2","3","6"):
            hr=[r for r in rows if r["value_count"]==str(count) and r["decimals"]==d];pr=[r for r in prow if r["value_count"]==str(count) and r["decimals"]==d]
            inherited=(float(r["encrypt_seconds"])+float(r["sum_evaluate_seconds"])+float(r["merge_seconds"]) for r in hr)
            mean_online=(float(r["encrypt_seconds"])+float(r["sum_evaluate_seconds"])+float(r["merge_seconds"])+float(r["mean_scale_seconds"])+float(r["mean_decrypt_seconds"]) for r in hr)
            lines.append(f"| {count} | {d} | {statistics.median(float(r['pandas_mean_seconds']) for r in pr):.9f} | {statistics.median(inherited):.9f} | {statistics.median(float(r['mean_scale_seconds']) for r in hr):.9f} | {statistics.median(float(r['mean_decrypt_seconds']) for r in hr):.9f} | {statistics.median(mean_online):.9f} | {max(float(r['mean_abs_error']) for r in hr):.12g} |")
    (root/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8");result={"status":"ckks_sum_mean_benchmark_executed","report":"REPORT.md","runner_wall_seconds":wall};write_json(root/"result.json",result);print(json.dumps(result,indent=2))
if __name__=="__main__":main()
