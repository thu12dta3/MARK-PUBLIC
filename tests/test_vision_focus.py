
import io
import sys
import unittest
from pathlib import Path
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mark.vision_focus import crop_focus, validate_focus, focus_instructions


class FocusTests(unittest.TestCase):
    def fixture(self):
        image=Image.new('RGB',(400,200),'blue');ImageDraw.Draw(image).rectangle((0,0,199,199),fill='red')
        stream=io.BytesIO();image.save(stream,'JPEG',quality=95);return stream.getvalue()

    def test_crop_uses_original_pixels_and_dimensions(self):
        with Image.open(io.BytesIO(crop_focus(self.fixture(),(0,0,.5,1)))) as image:
            self.assertEqual(image.size,(200,200))
            r,g,b=image.getpixel((50,50));self.assertGreater(r,200);self.assertLess(b,30)

    def test_none_preserves_scene(self):
        original=self.fixture();self.assertEqual(crop_focus(original,None),original)

    def test_coordinates_reject_nonfinite_outside_or_inverted(self):
        for rect in [(0,0,float('nan'),1),(0,0,float('inf'),1),(-.1,0,1,1),(0,0,1.1,1),
                     (.5,0,.2,1),(0,.5,1,.2),(0,0,0,1),(0,0,True,1),('0',0,1,1),(0,1),{},'a']:
            with self.subTest(rect=rect),self.assertRaises(ValueError):validate_focus(rect)

    def test_small_crop_rejected_without_upscaling(self):
        with self.assertRaisesRegex(ValueError,'maior'):crop_focus(self.fixture(),(0,0,.01,.01))

    def test_corrupt_frame_rejected(self):
        with self.assertRaises(ValueError):crop_focus(b'not an image',(0,0,.5,.5))

    def test_orientation_applied_before_coordinates(self):
        image=Image.new('RGB',(400,200),'cyan');exif=Image.Exif();exif[274]=6;stream=io.BytesIO()
        image.save(stream,'JPEG',exif=exif)
        with Image.open(io.BytesIO(crop_focus(stream.getvalue(),(0,0,1,.5)))) as crop:
            self.assertEqual(crop.size,(200,200));self.assertEqual(dict(crop.getexif()),{})

    def test_prompt_labels_pair_and_avoids_double_counting(self):
        rules=focus_instructions((0,0,.5,.5))
        self.assertIn('mesmo quadro',rules);self.assertIn('duplicado',rules)
        self.assertIn('conte apenas nessa área',rules);self.assertEqual(focus_instructions(None),'')
