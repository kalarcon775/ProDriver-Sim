# ProDriver Sim

**ProDriver Sim** is a physics-based LED driver and fixture simulation tool developed by an intern at **LUX Dynamics** to significantly speed up LED fixture testing and reduce iteration time between simulation and bench validation.

The tool models real LED drivers and LED boards using measured electrical curves, grid voltage variability, and realistic loss mechanisms. It allows engineers to predict output voltage, current, power, efficiency, and input power before hardware testing, enabling faster design decisions and better correlation with lab data.

---

## Key Features

- **Physics-based driver modeling**
  - Efficiency vs load, output voltage, and output power
  - Input voltage limits, power limiting, and output clipping
  - Fixed losses, AC line losses, and efficiency biasing

- **LED board modeling**
  - Interpolated IV curves
  - Series and parallel board configurations
  - Board-level voltage, current, and power breakdowns

- **Grid voltage calibration**
  - Input real measured grid voltages
  - Automatic outlier rejection
  - Runtime sampling of grid variability using mean and standard deviation

- **Fixture simulation**
  - Adjustable programmed current
  - Single-board or multi-board operation
  - Real-time status reporting for fault and limit conditions

- **Measured data comparison**
  - Compare simulated output voltage and input power against lab measurements
  - Instant delta calculations for validation and tuning

- **Live visualization**
  - Real-time output power plot
  - Automatic scaling based on driver power limits

- **Profile management**
  - Load and save driver, board, and fixture configurations via JSON
  - No source code changes required to add new hardware models

---

## Intended Use

This tool is designed for practical engineering workflows including:
- LED fixture design verification  
- Driver comparison and selection  
- Pre-test validation before lab bring-up  
- Simulation-to-measurement correlation  

It is not intended as an idealized academic simulator but as a fast, realistic engineering aid.

---

## Requirements

- Python 3.10+
- PyQt6
- NumPy
- SciPy
- Matplotlib

Install dependencies with:
```bashRunning the Application


Ensure driver and board JSON profiles are available in the working directory or loaded through the UI.

Extensibility
New LED drivers and boards can be added by defining their electrical characteristics in JSON files. Thermal modeling hooks are intentionally stubbed for future expansion.

Author
Kailani Alarcon
Engineering Intern, LUX Dynamics
pip install pyqt6 numpy scipy matplotlib
