// Image loading and optimization
class ImageLoader {
    constructor() {
        this.images = document.querySelectorAll('img[loading="lazy"]');
        this.init();
    }

    init() {
        if ('IntersectionObserver' in window) {
            this.lazyLoadImages();
        } else {
            this.loadImagesImmediately();
        }
    }

    lazyLoadImages() {
        const imageObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    this.loadImage(img);
                    imageObserver.unobserve(img);
                }
            });
        });

        this.images.forEach(img => imageObserver.observe(img));
    }

    loadImagesImmediately() {
        this.images.forEach(img => this.loadImage(img));
    }

    loadImage(img) {
        const src = img.getAttribute('src');
        const alt = img.getAttribute('alt');
        
        // Create a new image to preload
        const newImg = new Image();
        newImg.src = src;
        newImg.alt = alt;
        
        newImg.onload = () => {
            img.src = src;
            img.alt = alt;
            img.classList.add('loaded');
            img.classList.remove('lazy-load');
        };
        
        newImg.onerror = () => {
            console.warn(`Failed to load image: ${src}`);
            // You could set a placeholder image here
        };
    }
}

// Initialize image loader when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new ImageLoader();
});

// Add loading animation for images
document.addEventListener('DOMContentLoaded', function() {
    const images = document.querySelectorAll('img');
    
    images.forEach(img => {
        // Add loading class
        img.classList.add('lazy-load');
        
        // Check if image is already loaded (cached)
        if (img.complete) {
            img.classList.add('loaded');
        } else {
            img.addEventListener('load', function() {
                this.classList.add('loaded');
            });
            
            img.addEventListener('error', function() {
                console.warn('Image failed to load:', this.src);
                // You could set a fallback image here
            });
        }
    });
});