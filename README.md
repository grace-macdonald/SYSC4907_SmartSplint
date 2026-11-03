# SYSC4907_SmartSplint
Repository for software related to the Smart Splint project for SYSC4907

## RTOS Environment Setup

mkdir capstone && cd capstone
git clone https://github.com/grace-macdonald/SYSC4907_SmartSplint.git
python3 -m venv .venv && source .venv/bin/activate
pip install west
west init -l SYSC4907_SmartSplint
west update
west packages pip --install
west zephyr-export
west blobs fetch hal_stm32
cd zephyr && west sdk install && cd ..
west build -p always -b nucleo_wba55cg SYSC4907_SmartSplint/firmware
