// dark-mode.js

// Function to create and append the dark mode toggle button
function createDarkModeToggle() {
    const sidebarBottom = document.querySelector('.sidebar-bottom');
    if (!sidebarBottom) return;

    const darkModeToggle = document.createElement('div');
    darkModeToggle.className = 'theme-switch-wrapper';
    darkModeToggle.innerHTML = `
        <label class="theme-switch" for="checkbox">
            <input type="checkbox" id="checkbox" />
            <div class="slider round"></div>
        </label>
        <em>Dark Mode</em>
    `;

    sidebarBottom.appendChild(darkModeToggle);
}

// Function to initialize dark mode
function initDarkMode() {
    const toggleSwitch = document.querySelector('.theme-switch input[type="checkbox"]');
    const currentTheme = localStorage.getItem('theme');

    if (currentTheme) {
        document.documentElement.setAttribute('data-theme', currentTheme);
        if (currentTheme === 'dark') {
            toggleSwitch.checked = true;
            document.body.classList.add('dark-mode');
        }
    }

    function switchTheme(e) {
        if (e.target.checked) {
            document.documentElement.setAttribute('data-theme', 'dark');
            localStorage.setItem('theme', 'dark');
            document.body.classList.add('dark-mode');
        } else {
            document.documentElement.setAttribute('data-theme', 'light');
            localStorage.setItem('theme', 'light');
            document.body.classList.remove('dark-mode');
        }    
    }

    toggleSwitch.addEventListener('change', switchTheme, false);
}

// Function to add necessary styles
function addDarkModeStyles() {
    const style = document.createElement('style');
    style.textContent = `
        body.dark-mode {
            background-color: #1a1a1a;
            color: #ffffff;
        }
        body.dark-mode .sidebar {
            background-color: #2c2c2c;
        }
        body.dark-mode .sidebar-bottom {
            border-top-color: #444;
        }
        .theme-switch-wrapper {
            display: flex;
            align-items: center;
        }
        .theme-switch {
            display: inline-block;
            height: 34px;
            position: relative;
            width: 60px;
        }
        .theme-switch input {
            display: none;
        }
        .slider {
            background-color: #ccc;
            bottom: 0;
            cursor: pointer;
            left: 0;
            position: absolute;
            right: 0;
            top: 0;
            transition: .4s;
        }
        .slider:before {
            background-color: #fff;
            bottom: 4px;
            content: "";
            height: 26px;
            left: 4px;
            position: absolute;
            transition: .4s;
            width: 26px;
        }
        input:checked + .slider {
            background-color: #66bb6a;
        }
        input:checked + .slider:before {
            transform: translateX(26px);
        }
        .slider.round {
            border-radius: 34px;
        }
        .slider.round:before {
            border-radius: 50%;
        }
        .theme-switch-wrapper em {
            margin-left: 10px;
            font-size: 1rem;
        }
    `;
    document.head.appendChild(style);
}

// Main function to set up dark mode
function setupDarkMode() {
    addDarkModeStyles();
    createDarkModeToggle();
    initDarkMode();
}

// Run setup when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', setupDarkMode);