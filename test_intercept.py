from browser.session import BrowserManager
import time

print("Starting browser...")
bm = BrowserManager()
bm.start()

js_code = """
async () => {
    return new Promise((resolve) => {
        // Intercept grecaptcha.execute
        if (typeof grecaptcha !== 'undefined') {
            const originalExecute = grecaptcha.execute;
            grecaptcha.execute = function() {
                console.log("grecaptcha.execute called with arguments:", arguments);
                window._last_grecaptcha_args = Array.from(arguments);
                return originalExecute.apply(this, arguments);
            };
            resolve("Interceptor installed");
        } else {
            resolve("grecaptcha not found");
        }
    });
}
"""
print(bm.evaluate(js_code))

# Now trigger a click on a date to load traintrip
click_code = """
async () => {
    // Find a date element and click it
    const dateEl = document.querySelector('.date-item'); // Assuming there is some element like this
    if (dateEl) {
        dateEl.click();
        return "Clicked date element";
    }
    return "Date element not found";
}
"""
print(bm.evaluate(click_code))

time.sleep(3)

# Retrieve the captured args
args_code = """
async () => {
    return window._last_grecaptcha_args || "No args captured";
}
"""
print("Captured args:", bm.evaluate(args_code))

bm.stop()
print("Done.")
