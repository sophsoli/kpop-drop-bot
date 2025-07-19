import requests
from PIL import Image
from io import BytesIO


def apply_frame(card_path, frame_path):
    # # Download card image from URL
    # response = requests.get(card_url)
    # card = Image.open(BytesIO(response.content)).convert("RGBA")

    # Load Local Frame
    frame = Image.open(frame_path).convert("RGBA")

    # Load card image
    card_img = Image.open(card_path).convert("RGBA")

    #Resize card to match frame size
    resized_card = resize_and_pad_to_target(card_img, 1500, 2100)
    frame = frame.resize(resized_card.size)

    #Merge card and frame
    return Image.alpha_composite(resized_card, frame)

def merge_cards_horizontally(card_images, spacing=100, max_width=2000):
    widths, heights = zip(*(img.size for img in card_images))

    total_width = sum(widths) + spacing * (len(card_images) - 1)
    max_height = max(heights)

    merged_image = Image.new("RGBA", (total_width, max_height), (255, 255, 255, 0))

    x_offset = 0
    for img in card_images:
        merged_image.paste(img, (x_offset, 0), img)
        x_offset += img.width + spacing

    if merged_image.width > max_width:
        scale = max_width / merged_image.width
        new_size = (int(merged_image.width * scale), int(merged_image.height * scale))
        merged_image = merged_image.resize(new_size, Image.Resampling.LANCZOS)


    return merged_image

def resize_image(image, max_width):
    if image.width <= max_width:
        return image
    
    w_percent = max_width / float(image.width)
    new_height = int(float(image.height) * w_percent)

    return image.resize((max_width, new_height), Image.Resampling.LANCZOS)

def resize_and_pad_to_target(image, target_width=1500, target_height=2100):
    img_ratio = image.width / image.height
    target_ratio = target_width / target_height

    if img_ratio > target_ratio:
        new_height = target_height
        new_width = int(image.width * (target_height / image.height))
    else:
        new_width = target_width
        new_height = int(image.height * (target_width / image.width))

    image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    left = (new_width - target_width) // 2
    top = (new_height - target_height) // 2
    right = left + target_width
    bottom = top + target_height
    return image.crop((left, top, right, bottom))