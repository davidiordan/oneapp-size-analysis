from setuptools import setup, find_packages

setup(
    name="oneapp-size-analysis",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["cmpcodesize"],
    entry_points={
        "console_scripts": [
            "oneapp-size-analysis=oneapp_size_analysis.main:main",
        ],
    },
    python_requires=">=3.9",
)
