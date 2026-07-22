#!/usr/bin/env python3
"""Run all CKKS primitives over real prepared installment parent columns."""
from __future__ import annotations

import argparse, csv, json, shutil, statistics, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from code.heir.common import write_json
from code.heir.prepared_installments import load_prepared_parents, public_power_of_two_scale
from code.heir.scripts.run_payment_features_ciphertext_demo import copy_generated_sources, run

CMAKE=r'''cmake_minimum_required(VERSION 3.16)
project(real_installments_primitives LANGUAGES CXX)
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
RUNNER=r'''
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "add_output.h"
#include "sub_output.h"
#include "mul_output.h"
using namespace lbcrypto;
struct P{std::vector<double>a,b;}; double sec(std::chrono::steady_clock::time_point t){return std::chrono::duration<double>(std::chrono::steady_clock::now()-t).count();}
P read(const std::string& p){std::ifstream f(p);if(!f)throw std::runtime_error("cannot open "+p);std::string l;std::getline(f,l);P x;while(std::getline(f,l)){std::stringstream s(l);std::string a,b;std::getline(s,a,',');std::getline(s,b,',');x.a.push_back(std::stod(a));x.b.push_back(std::stod(b));}return x;}
std::vector<double> block(const std::vector<double>&v,size_t off,size_t n){std::vector<double>o(n,0);size_t k=std::min(n,v.size()-off);std::copy(v.begin()+off,v.begin()+off+k,o.begin());return o;}
int main(int c,char**v){if(c!=6)return 2;try{const size_t slots=@SLOTS@;const double scale=std::stod(v[2]);const int reps=std::stoi(v[3]);auto p=read(v[1]);auto t=std::chrono::steady_clock::now();auto ctx=encrypted_multiply__generate_crypto_context();auto keys=ctx->KeyGen();ctx=encrypted_multiply__configure_crypto_context(ctx,keys.secretKey);ctx=encrypted_add__configure_crypto_context(ctx,keys.secretKey);ctx=encrypted_subtract__configure_crypto_context(ctx,keys.secretKey);std::ofstream meta(v[5]);meta<<"{\"setup_seconds\":"<<sec(t)<<",\"input_scale\":"<<scale<<",\"slots\":"<<slots<<"}\n";std::ofstream out(v[4]);out<<"calculation,repetition,encrypt_seconds,evaluate_seconds,decrypt_seconds,mae,max_abs_error\n"<<std::setprecision(17);for(int r=0;r<reps;r++)for(auto op:{std::string("CT+CT"),std::string("CT-CT"),std::string("CTxCT"),std::string("CT+PT"),std::string("CTxPT")}){double e=0,q=0,d=0,err=0,mx=0;size_t n=0;for(size_t off=0;off<p.a.size();off+=slots){auto a=block(p.a,off,slots),b=block(p.b,off,slots);for(auto&x:a)x/=scale;for(auto&x:b)x/=scale;t=std::chrono::steady_clock::now();std::vector<double> z; if(op=="CT+CT"){auto x=encrypted_add__encrypt__arg0(ctx,a,keys.publicKey);auto y=encrypted_add__encrypt__arg1(ctx,b,keys.publicKey);e+=sec(t);t=std::chrono::steady_clock::now();auto o=encrypted_add(ctx,x,y);q+=sec(t);t=std::chrono::steady_clock::now();z=encrypted_add__decrypt__result0(ctx,o,keys.secretKey);d+=sec(t);}else if(op=="CT-CT"){auto x=encrypted_subtract__encrypt__arg0(ctx,a,keys.publicKey);auto y=encrypted_subtract__encrypt__arg1(ctx,b,keys.publicKey);e+=sec(t);t=std::chrono::steady_clock::now();auto o=encrypted_subtract(ctx,x,y);q+=sec(t);t=std::chrono::steady_clock::now();z=encrypted_subtract__decrypt__result0(ctx,o,keys.secretKey);d+=sec(t);}else if(op=="CTxCT"){auto x=encrypted_multiply__encrypt__arg0(ctx,a,keys.publicKey);auto y=encrypted_multiply__encrypt__arg1(ctx,b,keys.publicKey);e+=sec(t);t=std::chrono::steady_clock::now();auto o=encrypted_multiply(ctx,x,y);q+=sec(t);t=std::chrono::steady_clock::now();z=encrypted_multiply__decrypt__result0(ctx,o,keys.secretKey);d+=sec(t);}else if(op=="CT+PT"){auto x=encrypted_add__encrypt__arg0(ctx,a,keys.publicKey);auto y=ctx->MakeCKKSPackedPlaintext(b);e+=sec(t);t=std::chrono::steady_clock::now();auto result=ctx->EvalAdd(x[0],y);decltype(x) o{result};q+=sec(t);t=std::chrono::steady_clock::now();z=encrypted_add__decrypt__result0(ctx,o,keys.secretKey);d+=sec(t);}else{auto x=encrypted_multiply__encrypt__arg0(ctx,a,keys.publicKey);auto y=ctx->MakeCKKSPackedPlaintext(b);e+=sec(t);t=std::chrono::steady_clock::now();auto result=ctx->EvalMult(x[0],y);decltype(x) o{result};q+=sec(t);t=std::chrono::steady_clock::now();z=encrypted_multiply__decrypt__result0(ctx,o,keys.secretKey);d+=sec(t);}size_t k=std::min(slots,p.a.size()-off);for(size_t i=0;i<k;i++){double want=(op=="CT-CT"?p.a[off+i]-p.b[off+i]:op=="CT+CT"||op=="CT+PT"?p.a[off+i]+p.b[off+i]:p.a[off+i]*p.b[off+i]);double got=z[i]*(op=="CTxCT"||op=="CTxPT"?scale*scale:scale);double x=std::abs(got-want);err+=x;mx=std::max(mx,x);n++;}}out<<op<<','<<r+1<<','<<e<<','<<q<<','<<d<<','<<err/n<<','<<mx<<'\n';}return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
'''

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generated-dir',type=Path,required=True);p.add_argument('--prepared-dir',type=Path,default=Path('data/prepared/installments_columns'));p.add_argument('--value-count',dest='value_counts',nargs='+',type=int,default=[1000]);p.add_argument('--input-scale',type=float,default=0);p.add_argument('--repetitions',type=int,default=5);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--openfhe-dir',default='/usr/local/lib/OpenFHE');p.add_argument('--overwrite',action='store_true');a=p.parse_args();root=a.output_dir.resolve()
 if len(a.value_counts)>1:
  if root.exists():
   if not a.overwrite:raise FileExistsError(root)
   shutil.rmtree(root)
  root.mkdir(parents=True)
  runs=[]
  for count in a.value_counts:
   child=root/f'rows_{count}'
   command=[sys.executable,str(Path(__file__).resolve()),'--generated-dir',str(a.generated_dir.resolve()),'--prepared-dir',str(a.prepared_dir.resolve()),'--value-count',str(count),'--input-scale',str(a.input_scale),'--repetitions',str(a.repetitions),'--output-dir',str(child),'--openfhe-dir',a.openfhe_dir,'--overwrite']
   completed=subprocess.run(command,text=True,capture_output=True)
   if completed.returncode:raise RuntimeError(f"real primitive count {count} failed:\n{completed.stdout}{completed.stderr}")
   runs.append({'value_count':count,'directory':str(child.relative_to(root))})
  write_json(root/'batch_result.json',{'status':'real_installments_primitive_batch_executed','runs':runs})
  (root/'REPORT.md').write_text('# Real installments primitive batch\n\n'+'\n'.join(f"- `{run['value_count']}` real rows: `{run['directory']}/REPORT.md`" for run in runs)+'\n',encoding='utf-8')
  print((root/'batch_result.json').read_text());return
 a.value_count=a.value_counts[0]
 if root.exists():
  if not a.overwrite:raise FileExistsError(root)
  shutil.rmtree(root)
 root.mkdir(parents=True);parents=load_prepared_parents(a.prepared_dir.resolve(),a.value_count);scale=a.input_scale or public_power_of_two_scale(parents.payment,parents.installment);stage=root/'plaintext_inputs.csv'
 with stage.open('w',newline='',encoding='utf-8') as h:
  w=csv.writer(h);w.writerow(['AMT_INSTALMENT','AMT_PAYMENT']);w.writerows(zip(parents.installment,parents.payment))
 python_csv=root/'python_results.csv'
 with python_csv.open('w',newline='',encoding='utf-8') as h:
  writer=csv.writer(h);writer.writerow(['calculation','repetition','python_seconds'])
  operations=(('CT+CT',lambda left,right:left+right),('CT-CT',lambda left,right:left-right),('CTxCT',lambda left,right:left*right),('CT+PT',lambda left,right:left+right),('CTxPT',lambda left,right:left*right))
  # CSV loading, numeric sanitation, and the public CKKS normalization are
  # deliberately outside this timer. This is the original-value arithmetic
  # reference for the encrypted operation only.
  for label,operation in operations:
   for repetition in range(1,a.repetitions+1):
    started=time.perf_counter(); _=[operation(left,right) for left,right in zip(parents.installment,parents.payment)]
    writer.writerow([label,repetition,time.perf_counter()-started])
 manifest=json.loads((a.generated_dir/'generation_manifest.json').read_text());work=root/'runner';work.mkdir();wanted={'encrypted_add':'add','encrypted_subtract':'sub','encrypted_multiply':'mul'}
 for k in manifest['kernels']:
  if k['entry_function'] in wanted:copy_generated_sources((a.generated_dir/k['source']).parent,work,wanted[k['entry_function']])
 (work/'primitive_runner.cpp').write_text(RUNNER.replace('@SLOTS@','8192'),encoding='utf-8');(work/'CMakeLists.txt').write_text(CMAKE,encoding='utf-8');build=work/'build';run(['cmake','-S',str(work),'-B',str(build),f'-DOpenFHE_DIR={a.openfhe_dir}'],work);run(['cmake','--build',str(build),'--target','primitive_runner'],work);he=root/'heir_results.csv';meta=root/'execution.json';wall,log=run(['env','OMP_NUM_THREADS=1',str((build/'primitive_runner').resolve()),str(stage.resolve()),str(scale),str(a.repetitions),str(he.resolve()),str(meta.resolve())],work);(work/'runner.log').write_text(log,encoding='utf-8');rows=list(csv.DictReader(he.open()));python_rows=list(csv.DictReader(python_csv.open()));execution=json.loads(meta.read_text(encoding='utf-8'));lines=['# Real installments CKKS primitives','',f'Real prepared rows: `{a.value_count}`. Parents: `AMT_INSTALMENT`, `AMT_PAYMENT`. CT−CT is encrypted `PAYMENT_DIFF`. Public encoding scale: `{scale}`.','',f"One shared CKKS context/key setup: `{float(execution['setup_seconds']):.9f}` s. It is reported once and excluded from per-operation slowdown, since a pipeline reuses its context and keys.",'', '| Operation | Python calculation median (s) | HE encryption median (s) | HE evaluation median (s) | HE audit decryption median (s) | HE online median (s) | Eval ÷ Python | Online ÷ Python | Max absolute error |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
 for op in ('CT+CT','CT-CT','CTxCT','CT+PT','CTxPT'):
  r=[x for x in rows if x['calculation']==op];p=[x for x in python_rows if x['calculation']==op];python_seconds=statistics.median(float(x['python_seconds']) for x in p);encrypt=statistics.median(float(x['encrypt_seconds']) for x in r);evaluate=statistics.median(float(x['evaluate_seconds']) for x in r);decrypt=statistics.median(float(x['decrypt_seconds']) for x in r);online=encrypt+evaluate+decrypt;lines.append(f"| {op.replace('x','×')} | {python_seconds:.9f} | {encrypt:.9f} | {evaluate:.9f} | {decrypt:.9f} | {online:.9f} | {evaluate/python_seconds:.2f}× | {online/python_seconds:.2f}× | {max(float(x['max_abs_error']) for x in r):.12g} |")
 lines += ['', '`Eval ÷ Python` compares only the HE arithmetic operation with the Python list-comprehension arithmetic. `Online ÷ Python` includes HE encryption and audit decryption. CSV reading, null filtering, and public input scaling are excluded from both timers.', '', 'Raw timing and accuracy rows: `python_results.csv`, `heir_results.csv`, `execution.json`.']
 (root/'REPORT.md').write_text('\n'.join(lines)+'\n');write_json(root/'result.json',{'status':'real_installments_primitive_executed','source_batches':parents.files_used,'real_rows':a.value_count,'input_scale':scale,'runner_wall_seconds':wall,'setup_seconds':execution['setup_seconds'],'raw_results':{'python':'python_results.csv','he':'heir_results.csv'}});print((root/'result.json').read_text())
if __name__=='__main__':main()
