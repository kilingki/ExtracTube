"""CPU-only processor planning inside the existing runtime; no weights or inference."""
import json, sys, os, hashlib
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import transformers
from transformers import AutoProcessor
expected={"MODEL_PATH":"/models/qwen3.8-27b","SERVED_MODEL_NAME":"qwen3.8-27b","MAX_MODEL_LEN":"32768","MAX_OUTPUT_TOKENS":"4096","MODEL_PROFILE":"qwen3.8-27b"}
if any(os.environ.get(k)!=v for k,v in expected.items()) or json.loads(os.environ.get("MM_PROCESSOR_KWARGS","{}"))!={"max_pixels":262144}:raise RuntimeError("runtime_profile_mismatch")
processor = AutoProcessor.from_pretrained("/models/qwen3.8-27b", local_files_only=True, max_pixels=262144)
rows = []
for job in json.load(sys.stdin)["jobs"]:
    encoded = processor.apply_chat_template(job["body"]["messages"], tokenize=True,
        add_generation_prompt=True, return_dict=True, return_tensors="pt",
        enable_thinking=False, processor_kwargs={"images_kwargs":{"max_pixels":262144}})
    grid = encoded.get("image_grid_thw")
    rows.append({"job_id":job["job_id"],"input_tokens":int(encoded["input_ids"].shape[-1]),
                 "image_grid_thw":grid.tolist() if grid is not None else None})
print(json.dumps({"transformers":transformers.__version__,"processor":type(processor).__name__,
 "image_processor":type(processor.image_processor).__name__,
 "image_processor_size":vars(processor.image_processor.size),
 "template_sha256":hashlib.sha256(str(processor.chat_template).encode()).hexdigest(),
 "max_pixels":262144,"runtime_environment":expected,"weights_loaded":False,"jobs":rows}))
