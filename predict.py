import argparse
import time
from typing import List

import torch
from diffusers import (
    StableDiffusionPipeline,
    StableDiffusionImg2ImgPipeline,
    StableDiffusionInpaintPipelineLegacy,
)
from cog import BasePredictor, Input, Path

from models.stable_diffusion.generate import generate
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

from .models.stable_diffusion.constants import SD_MODEL_CACHE
from .models.swinir.constants import MODELS_SWINIR
from .models.nllb.constants import TRANSLATOR_MODEL_CACHE, TRANSLATOR_TOKENIZER_CACHE 
from lingua import LanguageDetectorBuilder

from models.swinir.upscale import upscale

class Predictor(BasePredictor):
    def setup(self):
        """Load the model into memory to make running multiple predictions efficient"""
        print("Loading Stable Diffusion v1.5 pipelines...")

        self.txt2img_pipe = StableDiffusionPipeline.from_pretrained(
            "runwayml/stable-diffusion-v1-5",
            cache_dir=SD_MODEL_CACHE,
            local_files_only=True,
        ).to("cuda")
        self.txt2img_pipe.enable_xformers_memory_efficient_attention()
        
        self.img2img_pipe = StableDiffusionImg2ImgPipeline(
            vae=self.txt2img_pipe.vae,
            text_encoder=self.txt2img_pipe.text_encoder,
            tokenizer=self.txt2img_pipe.tokenizer,
            unet=self.txt2img_pipe.unet,
            scheduler=self.txt2img_pipe.scheduler,
            safety_checker=self.txt2img_pipe.safety_checker,
            feature_extractor=self.txt2img_pipe.feature_extractor,
        ).to("cuda")
        self.img2img_pipe.enable_xformers_memory_efficient_attention()
        
        self.inpaint_pipe = StableDiffusionInpaintPipelineLegacy(
            vae=self.txt2img_pipe.vae,
            text_encoder=self.txt2img_pipe.text_encoder,
            tokenizer=self.txt2img_pipe.tokenizer,
            unet=self.txt2img_pipe.unet,
            scheduler=self.txt2img_pipe.scheduler,
            safety_checker=self.txt2img_pipe.safety_checker,
            feature_extractor=self.txt2img_pipe.feature_extractor,
        ).to("cuda")
        
        # For translation
        self.detect_language = LanguageDetectorBuilder.from_all_languages().with_preloaded_language_models().build()
        
        translate_model_name = "facebook/nllb-200-distilled-1.3B"
        self.translate_tokenizer = AutoTokenizer.from_pretrained(translate_model_name, cache_dir=TRANSLATOR_TOKENIZER_CACHE)
        self.translate_model = AutoModelForSeq2SeqLM.from_pretrained(translate_model_name, cache_dir=TRANSLATOR_MODEL_CACHE).to("cuda")
        
        parser = argparse.ArgumentParser()
        parser.add_argument('--task', type=str, default='real_sr', help='classical_sr, lightweight_sr, real_sr, '
                                                                        'gray_dn, color_dn, jpeg_car')
        parser.add_argument('--scale', type=int, default=1, help='scale factor: 1, 2, 3, 4, 8')  # 1 for dn and jpeg car
        parser.add_argument('--noise', type=int, default=15, help='noise level: 15, 25, 50')
        parser.add_argument('--jpeg', type=int, default=40, help='scale factor: 10, 20, 30, 40')
        parser.add_argument('--training_patch_size', type=int, default=128, help='patch size used in training SwinIR. '
                                                                                 'Just used to differentiate two different settings in Table 2 of the paper. '
                                                                                 'Images are NOT tested patch by patch.')
        parser.add_argument('--large_model', action='store_true',
                            help='use large model, only provided for real image sr')
        parser.add_argument('--model_path', type=str,
                            default=MODELS_SWINIR['real_sr']['large'])
        parser.add_argument('--folder_lq', type=str, default=None, help='input low-quality test image folder')
        parser.add_argument('--folder_gt', type=str, default=None, help='input ground-truth test image folder')
        
        self.swinir_args = parser.parse_args('')
        self.device = torch.device('cuda')

    @torch.inference_mode()
    @torch.cuda.amp.autocast()
    def predict(
        self,
        prompt: str = Input(description="Input prompt.", default=""),
        negative_prompt: str = Input(description="Input negative prompt.", default=""),
        width: int = Input(
            description="Width of output image.",
            choices=[128, 256, 384, 448, 512, 576, 640, 704, 768, 832, 896, 960, 1024],
            default=512,
        ),
        height: int = Input(
            description="Height of output image.",
            choices=[128, 256, 384, 448, 512, 576, 640, 704, 768, 832, 896, 960, 1024],
            default=512,
        ),
        init_image: Path = Input(
            description="Inital image to generate variations of. Will be resized to the specified width and height.",
            default=None,
        ),
        mask: Path = Input(
            description="Black and white image to use as mask for inpainting over init_image. Black pixels are inpainted and white pixels are preserved. Tends to work better with prompt strength of 0.5-0.7. Consider using https://replicate.com/andreasjansson/stable-diffusion-inpainting instead.",
            default=None,
        ),
        prompt_strength: float = Input(
            description="Prompt strength when using init image. 1.0 corresponds to full destruction of information in init image.",
            default=0.8,
        ),
        num_outputs: int = Input(
            description="Number of images to output. If the NSFW filter is triggered, you may get fewer outputs than this.",
            ge=1,
            le=10,
            default=1
        ),
        num_inference_steps: int = Input(
            description="Number of denoising steps", ge=1, le=500, default=50
        ),
        guidance_scale: float = Input(
            description="Scale for classifier-free guidance", ge=1, le=20, default=7.5
        ),
        scheduler: str = Input(
            default="K-LMS",
            choices=["DDIM", "K-LMS", "PNDM", "K_EULER", "K_EULER_ANCESTRAL"],
            description="Choose a scheduler. If you use an init image, PNDM will be used.",
        ),
        seed: int = Input(
            description="Random seed. Leave blank to randomize the seed.", default=None
        ),
        image_u: Path = Input(
            description="Input image for the upscaler (Swinir).", default=None
        ),
        task_u: str = Input(
            default="Real-World Image Super-Resolution-Large",
            choices=[
                'Real-World Image Super-Resolution-Large',
                'Real-World Image Super-Resolution-Medium',
                'Grayscale Image Denoising',
                'Color Image Denoising',
                'JPEG Compression Artifact Reduction'
            ],
            description="Task type for the upscaler (Swinir).",
        ),
        noise_u: int = Input(
            description='Noise level, activated for Grayscale Image Denoising and Color Image Denoising. It is for the upscaler (Swinir). Leave it as default or arbitrary if other tasks are selected.',
            choices=[15, 25, 50],
            default=15,
        ),
        jpeg_u: int = Input(
            description='Scale factor, activated for JPEG Compression Artifact Reduction. It is for the upscaler (Swinir). Leave it as default or arbitrary if other tasks are selected.',
            choices=[10, 20, 30, 40],
            default=40,
        ),
        process_type: str = Input(
            description="Choose a process type, Can be 'generate' or 'upscale'.",
            choices=["generate", "upscale"],
            default="generate",
        )
    ) -> List[Path]:
        if process_type == 'upscale':
            startTime = time.time()
            output_paths = upscale(self.swinir_args, self.device, task_u, image_u, noise_u, jpeg_u)
            endTime = time.time()
            print(f"-- Upscaled in: {endTime - startTime} sec. --")
            return output_paths

        else:
            """Run a single prediction on the model"""
            startTime = time.time()
            output_paths = generate(
                prompt,
                negative_prompt,
                width, height,
                init_image,
                mask,
                prompt_strength,
                num_outputs,
                num_inference_steps,
                guidance_scale,
                scheduler,
                seed,
                self.txt2img_pipe,
                self.img2img_pipe,
                self.inpaint_pipe,
                self.translate_model,
                self.translate_tokenizer,
                self.detect_language
            ) 
            endTime = time.time()
            print(f"-- Generated in: {endTime - startTime} sec. --")
            return output_paths