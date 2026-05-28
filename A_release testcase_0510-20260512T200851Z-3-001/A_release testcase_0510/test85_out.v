module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n7,
    n8,
    n6,
    n12,
    n11,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output n7;
  output n8;
  output n6;
  output n12;
  output n11;
  output n9;
  output n10;

  wire \$and$testcase/test85/test85.v:20$4_Y , \$and$testcase/test85/test85.v:31$17_Y , \$auto$rtlil.cc:3255:Not$47 , n13, n14, n15, n16, n17, n19, n30, n31, n40, n41, n42, n43, n56, n57, n65;

  and   \$and$testcase/test85/test85.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test85/test85.v:25$11  (n30, n2[1], n3[1]);
  and   \$and$testcase/test85/test85.v:28$14  (n40, n2[2], n3[2]);
  xor   \$xor$testcase/test85/test85.v:30$16  (n42, n2[2], n3[2]);
  and   \$and$testcase/test85/test85.v:31$17  (\$and$testcase/test85/test85.v:31$17_Y , n2[2], n5);
  or    \$or$testcase/test85/test85.v:29$15  (n41, n2[2], n5);
  or    \$or$testcase/test85/test85.v:17$2  (n14, n13, n4);
  xor   \$xor$testcase/test85/test85.v:26$12  (n31, n30, n5);
  not   \$not$testcase/test85/test85.v:31$18  (n43, \$and$testcase/test85/test85.v:31$17_Y );
  or    \$or$testcase/test85/test85.v:42$31  (n56, n40, n41);
  not   \$not$testcase/test85/test85.v:18$41  (n15, n14);
  or    \$or$testcase/test85/test85.v:27$13  (n7, n31, n4);
  or    \$or$testcase/test85/test85.v:43$32  (n57, n56, n42);
  or    \$or$testcase/test85/test85.v:19$3  (n16, n15, n5);
  or    \$or$testcase/test85/test85.v:44$33  (n8, n57, n43);
  and   \$and$testcase/test85/test85.v:20$4  (\$and$testcase/test85/test85.v:20$4_Y , n16, n2[1]);
  and   \$and$testcase/test85/test85.v:53$37  (n65, n31, n8);
  not   \$not$testcase/test85/test85.v:20$5  (n17, \$and$testcase/test85/test85.v:20$4_Y );
  dff g38 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  or    \$or$testcase/test85/test85.v:21$6  (\$auto$rtlil.cc:3255:Not$47 , n17, n3[1]);
  not   \$not$testcase/test85/test85.v:22$9  (n19, \$auto$rtlil.cc:3255:Not$47 );
  xor   \$xor$testcase/test85/test85.v:23$10  (n6, n19, n2[2]);
  xor   \$xor$testcase/test85/test85.v:55$38  (n12, n6, n7);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
