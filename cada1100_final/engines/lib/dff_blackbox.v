// Contest DFF blackbox for commercial EDA engines (DC / Genus / LEC).
// Ports follow the official Q&A A12: RN = active-low reset, SN = active-low
// set, CK = posedge clock, D = data, Q = output.  Engines must treat DFF
// instances as opaque combinational boundaries (Q = source, D = sink).
module dff(
  input RN,
  input SN,
  input CK,
  input D,
  output Q
);
endmodule
