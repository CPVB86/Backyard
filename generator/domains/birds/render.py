"""AvianVisitors demo/generate.py functions; only resource paths adapted."""
from io import BytesIO
from pathlib import Path
from . import helpers as pregen
HERE = Path(__file__).resolve().parent

def prepare_prompt(sci, com, pose, references):
    prompt = pregen.load_prompt(HERE / "prompt.template.md")
    prompt = prompt.replace("{sci_name}", sci).replace("{com_name}", com)
    prompt = prompt.replace("{pose}", pregen.POSES[pose]).replace("{anti_ref_line}", "")
    # The legacy template mixes IMAGE 2/3 for style. Bind references by role
    # instead, so optional anatomy/anti-reference files cannot shift meaning.
    prompt = prompt.replace("IMAGE 2", "the STYLE reference").replace("IMAGE 3", "the STYLE reference")
    prompt = prompt.replace("IMAGE 1", "the ANATOMY reference (if supplied; otherwise use the named species)")
    notes = pregen.load_species_notes(HERE / "species-notes.json")
    if sci in notes:
        prompt += "\nSpecies-specific note: " + notes[sci]
    prompt += "\n\nReference images, in upload order:\n" + "\n".join(
        f"Image {i + 1}: {label}" for i, (label, _) in enumerate(references))
    prompt += ("\nOUTPUT OVERRIDE: Ignore the cream-ground/background instructions above. "
               "Return one bird isolated on a fully transparent background with a clean alpha channel. "
               "No text, branches, shadow or border. Keep the entire bird including wings, tail and feet "
               "inside the image with generous transparent margins. The style reference supplies painting "
               "technique only; do not copy its species or anatomy.")
    return prompt

def save_cutout(data, destination):
    from PIL import Image
    image = Image.open(BytesIO(data)).convert("RGBA")
    alpha = image.getchannel("A")
    if alpha.getextrema()[0] > 0 or not alpha.getbbox():
        raise ValueError("Image lacks a transparent background or visible bird; raw image kept for review")
    box = alpha.getbbox()
    padding = max(8, round(max(box[2] - box[0], box[3] - box[1]) * .04))
    bird = image.crop(box)
    canvas = Image.new("RGBA", (bird.width + padding * 2, bird.height + padding * 2))
    canvas.paste(bird, (padding, padding))
    temporary = destination.with_suffix(".tmp.png")
    canvas.save(temporary)
    temporary.replace(destination)
