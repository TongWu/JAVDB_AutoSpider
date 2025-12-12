#!/usr/bin/env python3
"""
JavDB Captcha Solver

Supports multiple methods:
1. OCR (pytesseract) - Free, ~70% accuracy
2. Manual input - Fallback method
3. 2Captcha API (optional) - Paid, ~95% accuracy

Usage:
    from javdb_captcha_solver import solve_captcha
    captcha_code = solve_captcha(captcha_image_data, method='auto')
"""

import os
import sys
import io
from PIL import Image, ImageEnhance, ImageFilter

# Try to import optional dependencies
TESSERACT_AVAILABLE = False
try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    pass

TWOCAPTCHA_AVAILABLE = False
try:
    from twocaptcha import TwoCaptcha
    TWOCAPTCHA_AVAILABLE = True
except ImportError:
    pass


def preprocess_captcha_image(image_data):
    """
    Preprocess captcha image to improve OCR accuracy
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        PIL.Image: Preprocessed image
    """
    # Open image
    img = Image.open(io.BytesIO(image_data))
    
    # Convert to grayscale
    img = img.convert('L')
    
    # Enhance contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    # Sharpen
    img = img.filter(ImageFilter.SHARPEN)
    
    # Threshold to binary (black and white)
    threshold = 128
    img = img.point(lambda p: 255 if p > threshold else 0)
    
    # Resize to improve OCR (make it larger)
    width, height = img.size
    img = img.resize((width * 3, height * 3), Image.Resampling.LANCZOS)
    
    return img


def solve_with_tesseract(image_data, save_path='javdb_captcha.png'):
    """
    Solve captcha using Tesseract OCR
    
    Args:
        image_data: Raw image bytes
        save_path: Path to save preprocessed image
        
    Returns:
        tuple: (success: bool, captcha_code: str, confidence: float)
    """
    if not TESSERACT_AVAILABLE:
        return False, None, 0.0
    
    try:
        # Preprocess image
        img = preprocess_captcha_image(image_data)
        
        # Save preprocessed image for debugging
        if save_path:
            img.save(save_path)
        
        # Configure Tesseract for alphanumeric only
        # JavDB captcha is usually 5 lowercase letters
        custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyz'
        
        # Run OCR
        text = pytesseract.image_to_string(img, config=custom_config)
        
        # Clean up result
        captcha_code = ''.join(c for c in text if c.isalnum()).lower()
        
        # Validate result (JavDB captcha is typically 5 characters)
        if len(captcha_code) == 5:
            confidence = 0.7  # Estimated confidence for valid length
            return True, captcha_code, confidence
        else:
            # Try without preprocessing
            original_img = Image.open(io.BytesIO(image_data))
            text = pytesseract.image_to_string(original_img, config=custom_config)
            captcha_code = ''.join(c for c in text if c.isalnum()).lower()
            
            if len(captcha_code) == 5:
                return True, captcha_code, 0.5
            else:
                return False, captcha_code, 0.2
                
    except Exception as e:
        print(f"⚠️  Tesseract OCR failed: {e}")
        return False, None, 0.0


def solve_with_2captcha(image_data, api_key):
    """
    Solve captcha using 2Captcha service
    
    Args:
        image_data: Raw image bytes
        api_key: 2Captcha API key
        
    Returns:
        tuple: (success: bool, captcha_code: str, confidence: float)
    """
    if not TWOCAPTCHA_AVAILABLE:
        print("⚠️  2Captcha library not installed. Install: pip install 2captcha-python")
        return False, None, 0.0
    
    if not api_key:
        return False, None, 0.0
    
    try:
        solver = TwoCaptcha(api_key)
        
        # Save image temporarily
        temp_file = 'temp_captcha_2captcha.png'
        with open(temp_file, 'wb') as f:
            f.write(image_data)
        
        print("🌐 Submitting to 2Captcha service...")
        print("   (This may take 10-30 seconds)")
        
        # Solve captcha
        result = solver.normal(temp_file, numeric=0, minLen=5, maxLen=5)
        
        # Cleanup
        if os.path.exists(temp_file):
            os.remove(temp_file)
        
        captcha_code = result['code'].lower()
        
        if len(captcha_code) == 5:
            print(f"✓ 2Captcha solved: {captcha_code}")
            return True, captcha_code, 0.95
        else:
            return False, captcha_code, 0.5
            
    except Exception as e:
        print(f"⚠️  2Captcha failed: {e}")
        if os.path.exists('temp_captcha_2captcha.png'):
            os.remove('temp_captcha_2captcha.png')
        return False, None, 0.0


def solve_with_manual_input(image_data, save_path='javdb_captcha.png'):
    """
    Solve captcha with manual user input
    
    Args:
        image_data: Raw image bytes
        save_path: Path to save captcha image
        
    Returns:
        tuple: (success: bool, captcha_code: str, confidence: float)
    """
    try:
        # Save image
        with open(save_path, 'wb') as f:
            f.write(image_data)
        
        print(f"✓ Captcha image saved to: {save_path}")
        
        # Try to open the image automatically
        try:
            import platform
            system = platform.system()
            if system == 'Darwin':  # macOS
                os.system(f'open {save_path}')
            elif system == 'Linux':
                os.system(f'xdg-open {save_path} 2>/dev/null || cat {save_path}')
            elif system == 'Windows':
                os.system(f'start {save_path}')
        except:
            pass
        
        # Get user input
        captcha_code = input("🔐 Please enter the captcha code: ").strip().lower()
        
        if captcha_code and len(captcha_code) == 5:
            return True, captcha_code, 1.0  # Manual input has highest confidence
        else:
            return False, captcha_code, 0.0
            
    except Exception as e:
        print(f"⚠️  Manual input failed: {e}")
        return False, None, 0.0


def solve_captcha(image_data, method='auto', api_key=None, save_path='javdb_captcha.png', 
                  auto_confirm=True, confidence_threshold=0.6):
    """
    Solve captcha using specified method or auto-select best available
    
    Args:
        image_data: Raw image bytes
        method: 'auto', 'tesseract', '2captcha', 'manual'
        api_key: 2Captcha API key (if using 2captcha method)
        save_path: Path to save captcha image
        auto_confirm: If False, ask user to confirm OCR result
        confidence_threshold: Minimum confidence to auto-accept result
        
    Returns:
        str: Captcha code or None if failed
    """
    print("\n🔐 Solving captcha...")
    
    if method == 'auto':
        # Try methods in order of preference
        
        # 1. Try 2Captcha if API key provided
        if api_key and TWOCAPTCHA_AVAILABLE:
            print("  Method: 2Captcha API")
            success, code, confidence = solve_with_2captcha(image_data, api_key)
            if success and confidence >= confidence_threshold:
                return code
        
        # 2. Try Tesseract OCR
        if TESSERACT_AVAILABLE:
            print("  Method: Tesseract OCR (free)")
            success, code, confidence = solve_with_tesseract(image_data, save_path)
            
            if success and len(code) == 5:
                print(f"  ✓ OCR result: {code} (confidence: {confidence:.0%})")
                
                if confidence >= confidence_threshold:
                    if auto_confirm:
                        print("  Auto-accepting result...")
                        return code
                    else:
                        # Ask user to confirm
                        confirm = input(f"  Accept this result? [Y/n]: ").strip().lower()
                        if confirm in ['', 'y', 'yes']:
                            return code
                        else:
                            print("  Result rejected, switching to manual input...")
                else:
                    print(f"  ⚠️  Confidence too low ({confidence:.0%}), need manual verification")
                    confirm = input(f"  Accept '{code}' or enter correct code (or Enter to skip): ").strip().lower()
                    if confirm in ['', 'y', 'yes']:
                        return code
                    elif confirm:
                        return confirm
                    # Fall through to manual input
            else:
                if code:
                    print(f"  ⚠️  OCR result invalid: '{code}' (expected 5 chars)")
                else:
                    print(f"  ⚠️  OCR failed to recognize text")
        else:
            print("  ⚠️  Tesseract OCR not installed")
            print("     Install: brew install tesseract (macOS) or apt install tesseract-ocr (Linux)")
        
        # 3. Fall back to manual input
        print("  Method: Manual input")
        success, code, confidence = solve_with_manual_input(image_data, save_path)
        if success:
            return code
        else:
            return None
    
    elif method == 'tesseract':
        if not TESSERACT_AVAILABLE:
            print("❌ Tesseract not available. Install: pip install pytesseract")
            return None
        success, code, confidence = solve_with_tesseract(image_data, save_path)
        return code if success else None
    
    elif method == '2captcha':
        if not api_key:
            print("❌ 2Captcha API key not provided")
            return None
        if not TWOCAPTCHA_AVAILABLE:
            print("❌ 2Captcha library not installed. Install: pip install 2captcha-python")
            return None
        success, code, confidence = solve_with_2captcha(image_data, api_key)
        return code if success else None
    
    elif method == 'manual':
        success, code, confidence = solve_with_manual_input(image_data, save_path)
        return code if success else None
    
    else:
        print(f"❌ Unknown method: {method}")
        return None


# Test function
def test_solver():
    """Test the captcha solver with a sample image"""
    print("=" * 60)
    print("Captcha Solver Test")
    print("=" * 60)
    print()
    print("Available methods:")
    print(f"  - Tesseract OCR: {'✓ Available' if TESSERACT_AVAILABLE else '✗ Not installed'}")
    print(f"  - 2Captcha API: {'✓ Available' if TWOCAPTCHA_AVAILABLE else '✗ Not installed'}")
    print(f"  - Manual input: ✓ Always available")
    print()
    
    if not TESSERACT_AVAILABLE:
        print("To install Tesseract OCR:")
        print("  macOS:   brew install tesseract")
        print("  Linux:   sudo apt install tesseract-ocr")
        print("  Windows: Download from https://github.com/UB-Mannheim/tesseract/wiki")
        print("  Then:    pip install pytesseract pillow")


if __name__ == '__main__':
    test_solver()


