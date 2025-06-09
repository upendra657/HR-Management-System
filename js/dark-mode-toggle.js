// Dark mode toggle
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

// Function to update chart colors based on dark mode
function updateChartColors(isDarkMode) {
    const textColor = isDarkMode ? '#ffffff' : '#666';
    
    // Assuming you have global variables for your charts named barChart and pieChart
    if (typeof barChart !== 'undefined') {
        barChart.options.plugins.legend.labels.color = textColor;
        barChart.options.scales.x.ticks.color = textColor;
        barChart.options.scales.y.ticks.color = textColor;
        barChart.update();
    }
    
    if (typeof pieChart !== 'undefined') {
        pieChart.options.plugins.legend.labels.color = textColor;
        pieChart.update();
    }
}

// Call this function when toggling dark mode
toggleSwitch.addEventListener('change', function(e) {
    updateChartColors(e.target.checked);
});