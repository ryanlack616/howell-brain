# Dream Workstation Build

**Status:** Aspirational — documented for future reference  
**Created:** 2026-02-13  
**Last Updated:** 2026-02-13

---

## Philosophy

A single workstation optimized for local LLM inference, creative work (ComfyUI, image generation), ceramics database development, and long-term archival storage. NVMe for active workloads, enterprise HDD for cold storage.

---

## Full System Spec

```json
{
  "system": {
    "purpose": [
      "LLM inference (GPU-accelerated)",
      "AI research / development",
      "high-end workstation",
      "content creation",
      "multitasking"
    ],
    "cpu": {
      "manufacturer": "AMD",
      "model": "Ryzen 9 7950X",
      "cores": 16,
      "threads": 32,
      "base_clock_ghz": 4.5,
      "boost_clock_ghz": 5.7,
      "l3_cache_mb": 64,
      "tdp_w": 170,
      "socket": "AM5",
      "architecture": "Zen 4"
    },
    "motherboard": {
      "manufacturer": "ASUS",
      "model": "ROG Crosshair X670E Hero",
      "chipset": "X670E",
      "form_factor": "ATX",
      "socket": "AM5",
      "pci_express": {
        "gpu_slot": "PCIe 5.0 x16",
        "nvme_slots": ["PCIe 4.0 x4", "PCIe 4.0 x4", "PCIe 4.0 x4"]
      },
      "memory_support": {
        "type": "DDR5",
        "max_capacity_gb": 192,
        "overclocked_speed_mhz": "DDR5-6400+"
      },
      "networking": ["Wi-Fi 6E", "2.5G Ethernet"],
      "usb": ["USB4", "USB 3.2 Gen 2x2", "USB 3.2 Gen 2"]
    },
    "memory": {
      "manufacturer": "Samsung",
      "model": "DDR5 6400 CUDIMM",
      "capacity_gb": 128,
      "configuration": "2x64GB",
      "speed_mhz": 6400,
      "timings": "CL52",
      "voltage_v": 1.10
    },
    "gpu": {
      "manufacturer": "NVIDIA",
      "model": "GeForce RTX 5090",
      "variant": "MSI SUPRIM LIQUID SOC",
      "vram_gb": 32,
      "memory_type": "GDDR7",
      "pci_express": "PCIe 5.0 x16",
      "peak_fp32_tflops": 104.9,
      "tensor_perf_fp16_tflops": "~1676",
      "int8_tops": "~3352"
    },
    "storage": {
      "drive_1": {
        "purpose": "OS + tools",
        "model": "Samsung 990 PRO 2TB",
        "interface": "PCIe 4.0 x4 NVMe",
        "seq_read_mb_s": 7450,
        "seq_write_mb_s": 6900,
        "filesystem": "ext4",
        "mount_point": "/"
      },
      "drive_2": {
        "purpose": "Active models + datasets",
        "model": "Samsung 990 PRO 4TB",
        "interface": "PCIe 4.0 x4 NVMe",
        "seq_read_mb_s": 7450,
        "seq_write_mb_s": 6900,
        "filesystem": "XFS",
        "mount_point": "/data/active"
      },
      "drive_3": {
        "purpose": "Archive / cold storage",
        "model": "WD Gold 16TB",
        "interface": "SATA III",
        "rpm": 7200,
        "seq_read_mb_s": 260,
        "seq_write_mb_s": 260,
        "filesystem": "XFS",
        "mount_point": "/data/archive",
        "mount_options": ["defaults", "noatime"]
      }
    },
    "power_supply": {
      "recommended_wattage_w": 1200,
      "efficiency": "80+ Gold or better",
      "features": ["ATX 3.0", "PCIe 5.0 / 12VHPWR support"]
    },
    "cooling": {
      "cpu_cooler": "360mm AIO or strong tower cooler",
      "case_airflow": "Multiple intake + exhaust fans"
    },
    "case": {
      "type": "Full tower / high-airflow ATX"
    },
    "backup_strategy": {
      "local_snapshots": "borg or restic to snapshot /data/archive",
      "offline_backups": "rsync to external HDD or cloud"
    }
  }
}
```

## Archive HDD Extended Specs

```json
{
  "product_line": "WD Gold",
  "recording_technology": "CMR (Conventional Magnetic Recording)",
  "cache_mb": 512,
  "workload_rating_tb_per_year": 550,
  "mtbf_hours": 2500000,
  "power_on_hours_per_year": 8760,
  "operating_mode": "24x7",
  "vibration_protection": "enterprise-grade rotational vibration safeguards",
  "error_recovery": "enterprise firmware with optimized error recovery",
  "power_consumption_w": { "idle": 5.0, "read_write": 6.5, "standby_sleep": 0.8 },
  "operating_temperature_c": { "min": 5, "max": 60 },
  "dimensions_mm": { "length": 147, "width": 101.6, "height": 26.1 },
  "weight_g": 670,
  "warranty_years": 5
}
```

---

## Notes

- No timeline pressure — this is a "when the budget allows" plan
- All components chosen, ready to purchase when opportunity arises
