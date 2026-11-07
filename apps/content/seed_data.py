"""Sample blog articles for local development (see the seed_articles command)."""

SAMPLES = [
    {
        "slug": "choosing-your-first-robot-kit",
        "title": "How to choose your first robot kit",
        "excerpt": "Motors, controllers and sensors explained, so you buy once and build more.",
        "category": "Guides",
        "author": "Stem Robo Hub Team",
        "color": "#1d4ed8",
        "days_ago": 20,
        "body": (
            "<p>Starting out in robotics is easier when the kit matches what you want to learn.</p>"
            "<h2>Start with the goal</h2>"
            "<p>Decide whether you want to learn <strong>electronics</strong>, <strong>coding</strong> or "
            "<strong>mechanics</strong>, then pick a kit that covers it.</p>"
            "<ul><li>Motors and a motor driver</li><li>A controller board</li><li>Basic sensors</li></ul>"
            "<h2>Check the learning material</h2>"
            '<p>A good kit ships with a build guide and example code. See our <a href="/products">products</a> '
            "for kits with guides included.</p>"
        ),
    },
    {
        "slug": "esp32-vs-arduino-uno-r4",
        "title": "ESP32 vs Arduino UNO R4: which should you pick?",
        "excerpt": "A practical comparison of speed, connectivity and ecosystem for robotics.",
        "category": "Comparison",
        "author": "Aarav Mehta",
        "color": "#0f766e",
        "days_ago": 34,
        "body": (
            "<p>Both boards can run a robot. They differ in what they make easy.</p>"
            "<h2>Speed and memory</h2><p>The ESP32 is much faster and has far more memory, which helps with "
            "sensor fusion and cameras.</p>"
            "<h2>Connectivity</h2><p>The ESP32 has Wi-Fi and Bluetooth built in. The UNO R4 is simpler to start "
            "with and has a large shield ecosystem.</p>"
            "<blockquote>Pick the ESP32 for connected projects, the UNO R4 for learning the basics.</blockquote>"
        ),
    },
    {
        "slug": "build-a-line-follower-in-an-hour",
        "title": "Build a line follower robot in under an hour",
        "excerpt": "Wiring, calibration and PID tuning from scratch, with code.",
        "category": "Tutorials",
        "author": "Isha Nair",
        "color": "#b45309",
        "days_ago": 47,
        "body": (
            "<p>A line follower is the classic first robot: two motors, two or more IR sensors and a controller.</p>"
            "<h2>What you need</h2><ol><li>A controller board</li><li>A motor driver</li><li>Two IR sensors</li>"
            "<li>Two geared motors and a chassis</li></ol>"
            "<h2>Wire it up</h2><p>Connect the sensors to two input pins and the motor driver to the PWM pins, "
            "then calibrate the sensors on your track.</p>"
            "<h2>Tune the steering</h2><p>Start with proportional control, then add a little derivative to damp "
            "the wobble.</p>"
        ),
    },
]
