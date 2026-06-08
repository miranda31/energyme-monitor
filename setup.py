from setuptools import setup, find_packages

setup(
    name="energyme-monitor",
    version="1.0.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=["pyramid", "pyramid-jinja2", "waitress", "requests"],
    entry_points={
        "paste.app_factory": ["main = energyme:main"],
    },
)
