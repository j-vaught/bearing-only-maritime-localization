# Charleston zoom previews

These two static frames keep the approved layout, map center, legend placement and transparent compass roses. At the 312.5 m generation, the Charleston view is zoomed two times relative to the original 24 km by 20 km crop. At the 156.25 m generation it is zoomed another two times, giving four times total zoom.

The corresponding Charleston extents are 12 km by 10 km and 6 km by 5 km. The fixed coast overview and global retained/rejected counts use the original recorded generation. Only the presentation crop changes. The full video and search results are unchanged.

The PNG and PDF files are ready for review or insertion into the presentation. To reproduce them from the local package, run `uv run --no-project --with numpy --with shapely --with pyproj python pruning/layout-previews/review-02/build_zoom_frames.py`. The included Typst template preserves this layout revision.
