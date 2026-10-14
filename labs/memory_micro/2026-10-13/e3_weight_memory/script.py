"""Run a scoped, local pretrained-model memory screen."""
from pathlib import Path
import os, sys, time, json, hashlib, traceback, random, platform

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "deps"))
os.environ["USE_TF"] = "0"
os.environ["USE_FLAX"] = "0"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["HF_HUB_DISABLE_XET"] = "1"
START = time.monotonic()
PROTOCOL_BYTES = (ROOT / "protocol.json").read_bytes()
P = json.loads(PROTOCOL_BYTES)
R = {"experiment": P["experiment"], "protocol_sha256": hashlib.sha256(PROTOCOL_BYTES).hexdigest(), "status": "running", "variants": {}, "blind_spots": P["blind_spots"]}

def save():
    R["elapsed_seconds"] = time.monotonic() - START
    (ROOT / "results.json").write_text(json.dumps(R, indent=2) + "\n")

def bytes_in(path):
    return sum(x.stat().st_size for x in path.rglob("*") if x.is_file())

def main():
    R["setup"] = {"dependency_directory_bytes": bytes_in(ROOT / "deps"), "model_directory_bytes": bytes_in(ROOT / "model"), "setup_cap_seconds": 180, "weight_file_present": (ROOT / "model" / "model.safetensors").exists()}
    if not (ROOT / "model" / "model.safetensors").exists():
        raise RuntimeError("The pretrained weight download did not complete within the 180-second setup cap. No training or capability evaluation was run.")
    import torch
    from torch import nn
    from transformers import AutoTokenizer, AutoModelForCausalLM
    torch.manual_seed(P["seed"])
    random.seed(P["seed"])
    torch.set_num_threads(6)
    R["environment"] = {"python": sys.version, "torch": torch.__version__, "machine": platform.machine(), "device": "cpu", "mps_available": torch.backends.mps.is_available(), "threads": torch.get_num_threads()}
    R["setup"] = {"dependency_directory_bytes": bytes_in(ROOT / "deps"), "model_directory_bytes": bytes_in(ROOT / "model")}
    t = time.monotonic()
    tok = AutoTokenizer.from_pretrained(ROOT / "model", local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(ROOT / "model", local_files_only=True, torch_dtype=torch.float32)
    tok.padding_side = "left"
    if tok.pad_token_id is None: tok.pad_token = tok.eos_token
    R["model"] = {"id": P["model"], "total_base_parameters": sum(x.numel() for x in model.parameters()), "base_tensor_bytes_float32": sum(x.numel()*x.element_size() for x in model.parameters()), "load_seconds": time.monotonic()-t}
    system = "Answer questions about the fictional Neris project. Give one short answer. If a fact is not known, say Unknown."

    def prompt(query, history=False):
        messages = [{"role":"system", "content":system}]
        if history:
            messages.append({"role":"user", "content":"Project record:\n"+"\n".join(P["source_history"])})
            messages.append({"role":"assistant", "content":"I will use this record."})
        messages.append({"role":"user", "content":query})
        return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    def score(output, test, second=False):
        s = output.lower().strip()
        required = list(test.get("required", []))
        forbidden = list(test.get("forbidden", []))
        if second and test["id"] in {"corrected_power", "power_paraphrase"}:
            required=["19"]; forbidden=["12", "17", "3"]
        return all(v.lower() in s for v in required) and not any(v.lower() in s for v in forbidden) and (not test.get("required_any") or any(v.lower() in s for v in test["required_any"]))

    def evaluate(name, history=False, second=False):
        t = time.monotonic(); model.eval()
        texts = [prompt(test["query"], history) for test in P["tests"]]
        inputs = tok(texts, padding=True, return_tensors="pt")
        with torch.no_grad():
            outputs = model.generate(**inputs, do_sample=False, max_new_tokens=P["generation"]["max_new_tokens"], pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id)
        answers = tok.batch_decode(outputs[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        rows = [{"id":test["id"], "query":test["query"], "output":answer, "passed":score(answer,test,second)} for test, answer in zip(P["tests"],answers)]
        R["variants"][name] = {"checks":rows, "pass_count":sum(r["passed"] for r in rows), "total_checks":len(rows), "all_required_passed":all(r["passed"] for r in rows), "seconds":time.monotonic()-t, "input_tokens":int(inputs["attention_mask"].sum()), "output_tokens":sum(len(tok.encode(a, add_special_tokens=False)) for a in answers)}
        print(name, R["variants"][name]["pass_count"], "/", len(rows), flush=True)
        save()

    evaluate("baseline_no_history")
    evaluate("baseline_with_history", True)

    class LoRA(nn.Module):
        def __init__(self, base):
            super().__init__(); self.base=base
            rank=P["adapter"]["rank"]
            self.a=nn.Parameter(torch.empty(rank,base.in_features)); self.b=nn.Parameter(torch.zeros(base.out_features,rank))
            nn.init.normal_(self.a,std=0.02)
            self.scale=P["adapter"]["alpha"]/rank
        def forward(self,x):
            return self.base(x)+(x@self.a.T@self.b.T)*self.scale

    for param in model.parameters(): param.requires_grad=False
    modules=[]
    layers=model.model.layers
    for index in range(len(layers)-P["adapter"]["last_layers"],len(layers)):
        layer=layers[index]
        for block_name,module_name in [("self_attn","q_proj"),("self_attn","v_proj"),("mlp","down_proj")]:
            block=getattr(layer,block_name); adapter=LoRA(getattr(block,module_name)); setattr(block,module_name,adapter)
            modules.append((f"model.layers.{index}.{block_name}.{module_name}",adapter))
    trainable=[param for param in model.parameters() if param.requires_grad]
    initial={name:{"a":mod.a.detach().clone(),"b":mod.b.detach().clone()} for name,mod in modules}
    R["adapter"]={"modules":[name for name,_ in modules], "trainable_parameters":sum(x.numel() for x in trainable), "tensor_bytes_float32":sum(x.numel()*x.element_size() for x in trainable), "rank":P["adapter"]["rank"], "base_frozen":True}
    save()

    def reset():
        with torch.no_grad():
            for name,mod in modules:
                mod.a.copy_(initial[name]["a"]); mod.b.copy_(initial[name]["b"])

    def train(name,kind,qa=None):
        t=time.monotonic(); model.train(); optimizer=torch.optim.AdamW(trainable,lr=P["update_budget"]["learning_rate"])
        examples=[]
        if kind=="raw":
            ids=tok.encode("\n".join(P["source_history"]),add_special_tokens=False)+[tok.eos_token_id]
            cap=P["update_budget"]["max_training_tokens_per_example"]
            examples=[(ids[i:i+cap],ids[i:i+cap]) for i in range(0,len(ids),cap) if len(ids[i:i+cap])>2]
        else:
            for query,answer in qa:
                prefix=tok.encode(prompt(query),add_special_tokens=False)
                suffix=tok.encode(answer,add_special_tokens=False)+[tok.eos_token_id]
                ids=prefix+suffix; labels=[-100]*len(prefix)+suffix
                cap=P["update_budget"]["max_training_tokens_per_example"]
                examples.append((ids[-cap:],labels[-cap:]))
        tokens=0; predicted=0; losses=[]
        for step in range(P["update_budget"]["steps"]):
            selected=[examples[(step*P["update_budget"]["batch_size"]+j)%len(examples)] for j in range(P["update_budget"]["batch_size"])]
            length=max(len(ids) for ids,_ in selected)
            inputs=torch.tensor([ids+[tok.pad_token_id]*(length-len(ids)) for ids,_ in selected]); labels=torch.tensor([lab+[-100]*(length-len(lab)) for _,lab in selected]); mask=torch.tensor([[1]*len(ids)+[0]*(length-len(ids)) for ids,_ in selected])
            optimizer.zero_grad(set_to_none=True)
            output=model(input_ids=inputs,attention_mask=mask,labels=labels,use_cache=False)
            output.loss.backward(); torch.nn.utils.clip_grad_norm_(trainable,1.0); optimizer.step()
            losses.append(float(output.loss.detach())); tokens+=int(mask.sum()); predicted+=int((labels[:,1:]!=-100).sum())
            if step%10==0: print(name,"step",step,"loss",losses[-1],flush=True)
        adapter_path=ROOT/(name+"_adapter.pt")
        torch.save({name:{"a":mod.a.detach(),"b":mod.b.detach()} for name,mod in modules},adapter_path)
        R.setdefault("updates",{})[name]={"seconds":time.monotonic()-t,"steps":len(losses),"input_tokens_seen":tokens,"target_tokens_seen":predicted,"losses":losses,"adapter_file_bytes":adapter_path.stat().st_size}
        save()

    reset(); train("raw_chat_update","raw"); evaluate("raw_chat_update")
    reset(); train("verified_qa_update","qa",P["training_qa"]); evaluate("verified_qa_update")
    train("qa_second_update","qa",P["second_update_qa"]); evaluate("qa_second_update",second=True)
    R["status"]="completed"
    R["interpretation"]="This is a single-seed capacity screen. A lexical pass is not proof of faithful semantic memory, source retention, abstraction, or successful large-agent continuation."
    save()

try:
    main()
except Exception:
    R["status"]="inconclusive"
    R["error"]=traceback.format_exc()
    save(); print(R["error"],flush=True)
finally:
    if (ROOT/"protocol.json").read_bytes()!=PROTOCOL_BYTES:
        R["status"]="invalid_protocol_changed"; save()
    lines=["E3 weight memory", "Status: "+R["status"], "Protocol SHA256: "+R["protocol_sha256"], "Elapsed seconds: "+str(round(R["elapsed_seconds"],2))]
    for name,value in R["variants"].items(): lines.append(f"{name}: {value['pass_count']}/{value['total_checks']} lexical checks; {value['seconds']:.2f} seconds.")
    lines.append(R.get("interpretation","The pretrained run did not complete. E3 is inconclusive."))
    lines.extend("Limit: "+x for x in R["blind_spots"])
    if R.get("error"): lines.append(R["error"])
    (ROOT/"report.txt").write_text("\n".join(lines)+"\n")
