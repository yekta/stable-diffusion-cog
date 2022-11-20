from diffusers import (
    PNDMScheduler,
    LMSDiscreteScheduler,
    DDIMScheduler,
    EulerDiscreteScheduler,
    EulerAncestralDiscreteScheduler
)
from .constants import SD_MODEL_CACHE


def make_scheduler(name):
    return {
        "PNDM": PNDMScheduler.from_config(
            "runwayml/stable-diffusion-v1-5",
            cache_dir=SD_MODEL_CACHE, 
            local_files_only=True, 
            subfolder="scheduler"
        ),
        "K-LMS": LMSDiscreteScheduler.from_config(
            "runwayml/stable-diffusion-v1-5",
            cache_dir=SD_MODEL_CACHE,
            local_files_only=True,
            subfolder="scheduler"
        ),
        "DDIM": DDIMScheduler.from_config(
            "runwayml/stable-diffusion-v1-5",
            cache_dir=SD_MODEL_CACHE,
            local_files_only=True,
            subfolder="scheduler"
        ),
        "K_EULER": EulerDiscreteScheduler.from_config(
            "runwayml/stable-diffusion-v1-5",
            cache_dir=SD_MODEL_CACHE, 
            local_files_only=True, 
            subfolder="scheduler"
        ),
        "K_EULER_ANCESTRAL": EulerAncestralDiscreteScheduler.from_config(
            "runwayml/stable-diffusion-v1-5",
            cache_dir=SD_MODEL_CACHE, 
            local_files_only=True,
            subfolder="scheduler"
        ),
    }[name]