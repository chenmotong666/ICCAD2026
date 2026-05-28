module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n12,
    n11,
    n6,
    n7,
    n8,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output [3:0] n12;
  output n11;
  output n6;
  output n7;
  output n8;
  output n9;
  output n10;

  wire \$and$testcase/test95/test95.v:29$12_Y , n13, n14, n15, n16, n17, n30, n31, n40, n41, n42, n43, n56, n57, n65;

  and   \$and$testcase/test95/test95.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test95/test95.v:23$6  (n30, n2[1], n3[1]);
  and   \$and$testcase/test95/test95.v:26$9  (n40, n2[2], n3[2]);
  xor   \$xor$testcase/test95/test95.v:28$11  (n42, n2[2], n3[2]);
  and   \$and$testcase/test95/test95.v:29$12  (\$and$testcase/test95/test95.v:29$12_Y , n2[2], n5);
  or    \$or$testcase/test95/test95.v:27$10  (n41, n2[2], n5);
  or    \$or$testcase/test95/test95.v:17$2  (n14, n13, n4);
  xor   \$xor$testcase/test95/test95.v:24$7  (n31, n30, n5);
  not   \$not$testcase/test95/test95.v:29$13  (n43, \$and$testcase/test95/test95.v:29$12_Y );
  or    \$or$testcase/test95/test95.v:40$26  (n56, n40, n41);
  not   \$not$testcase/test95/test95.v:18$35  (n15, n14);
  or    \$or$testcase/test95/test95.v:25$8  (n7, n31, n4);
  or    \$or$testcase/test95/test95.v:41$27  (n57, n56, n42);
  xor   \$xor$testcase/test95/test95.v:19$3  (n16, n15, n5);
  or    \$or$testcase/test95/test95.v:42$28  (n8, n57, n43);
  and   \$and$testcase/test95/test95.v:20$4  (n17, n16, n2[1]);
  and   \$and$testcase/test95/test95.v:51$32  (n65, n31, n8);
  or    \$or$testcase/test95/test95.v:21$5  (n6, n17, n3[1]);
  dff g36 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));

  assign n12[0] = n6;
  assign n12[1] = n7;
  assign n12[2] = n8;
  assign n9 = 1'b1;
  assign n10 = n4;
  assign n12[3] = 1'b1;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
