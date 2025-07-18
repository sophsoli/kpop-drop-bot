import requests
from PIL import Image
from io import BytesIO

def apply_frame(card_url, frame_path):
    # Download card image from URL
    response = requests.get(card_url)
    card = Image.open(BytesIO(response.content)).convert("RGBA")

    # Load Local Frame
    frame = Image.open(frame_path).convert("RGBA")

    #Resize card to match frame size
    card = card.resize(frame.size)

    #Merge card and frame
    return Image.alpha_composite(card, frame)

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