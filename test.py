import matplotlib.pyplot as plt
import numpy as np

# Data Setup for 4 Years (2026-2029)
years = ['2026', '2027', '2028', '2029']

# Non-Recurring Costs (NRC) - Total €11M Project Budget
# Allocation strategy:
# - Master Image (€2M): Front-loaded in 2026/2027
# - Deployment (€8.5M): Ramps up in 2027, peaks 2028, finishes 2029
# - Mgmt (€0.5M): Spread evenly
nrc_master = np.array([1.5, 0.5, 0.0, 0.0])
nrc_deploy = np.array([0.5, 2.5, 4.0, 1.5])
nrc_mgmt   = np.array([0.1, 0.1, 0.2, 0.1])

# Recurring Costs (RC) - Baseline €100M
# Scenario: RC stays high during transition, then drops as legacy is retired.
rc_costs = [100, 100, 98, 95]

# Plotting
fig, ax1 = plt.subplots(figsize=(10, 6))

# Stacked Bar Chart for NRC (Left Axis)
p1 = ax1.bar(years, nrc_master, label='Master Image Creation (€2M)', color='#1f77b4')
p2 = ax1.bar(years, nrc_deploy, bottom=nrc_master, label='Mass Deployment & Rollout (€8.5M)', color='#ff7f0e')
p3 = ax1.bar(years, nrc_mgmt, bottom=nrc_master+nrc_deploy, label='Mgmt & Contingency (€0.5M)', color='#2ca02c')

# Formatting Left Axis (NRC)
ax1.set_ylabel('Non-Recurring Costs (NRC) - Project Investment (M€)', color='#333333', fontsize=12, fontweight='bold')
ax1.set_xlabel('Year', fontsize=12)
ax1.tick_params(axis='y', labelcolor='#333333')
ax1.set_ylim(0, 6) # Scale to fit the highest bar nicely

# Line Chart for RC (Right Axis)
ax2 = ax1.twinx()
ax2.plot(years, rc_costs, color='#d62728', linewidth=3, marker='o', markersize=8, label='Recurring Costs (RC)')

# Formatting Right Axis (RC)
ax2.set_ylabel('Recurring Costs (RC) - Operational Run (M€)', color='#d62728', fontsize=12, fontweight='bold')
ax2.tick_params(axis='y', labelcolor='#d62728')
ax2.set_ylim(80, 110) # Focus on the 100M baseline drop

# Title and Legend
plt.title('Project Investment (NRC) vs. Recurring Cost (RC) Evolution\n(2026 - 2029)', fontsize=14, pad=20)
lines, labels = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines + lines2, labels + labels2, loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=2, frameon=False)

# Grid and Value Labels
ax1.grid(axis='y', linestyle='--', alpha=0.5)

# Add Total NRC Labels on top of bars
total_nrc = nrc_master + nrc_deploy + nrc_mgmt
for i, v in enumerate(total_nrc):
    ax1.text(i, v + 0.1, f"€{v:.1f}M", ha='center', va='bottom', fontweight='bold', color='#333333')

# Add RC Labels near points
for i, v in enumerate(rc_costs):
    ax2.text(i, v + 1.5, f"€{v}M", ha='center', va='bottom', fontweight='bold', color='#d62728')

plt.tight_layout()
plt.savefig('nrc_vs_rc_chart.png')