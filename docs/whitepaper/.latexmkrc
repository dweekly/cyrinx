# Keep LaTeX build artifacts out of the source tree: everything lands in out/,
# and the finished PDF is copied back beside the .tex (the PDF is tracked; the
# rest of out/ is gitignored). See docs/whitepaper/README is in the main README.
$pdf_mode = 1;
$out_dir  = 'out';
END { system("cp $out_dir/*.pdf . 2>/dev/null") if defined $out_dir; }
