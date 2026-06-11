from setuptools import setup

setup(
    name="mimo-console",
    version="0.1.0",
    py_modules=["mimo_stat"],
    install_requires=[
        "requests>=2.31.0",
        "psutil>=5.9.0",
    ],
    entry_points={
        "console_scripts": [
            "mimo-stat=mimo_stat:main",
        ],
    },
)
