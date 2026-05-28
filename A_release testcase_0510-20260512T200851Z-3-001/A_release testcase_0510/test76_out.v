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

  wire \$and$testcase/test76/test76.v:34$21_Y , \$or$testcase/test76/test76.v:24$10_Y , \$xor$testcase/test76/test76.v:25$12_Y , n20, n21, n22, n30, n31, n40, n41, n42, n43, n56, n57, n65;

  not   \$not$testcase/test76/test76.v:23$9  (n20, n2[2]);
  and   \$and$testcase/test76/test76.v:28$15  (n30, n2[1], n3[1]);
  and   \$and$testcase/test76/test76.v:31$18  (n40, n2[2], n3[2]);
  xor   \$xor$testcase/test76/test76.v:33$20  (n42, n2[2], n3[2]);
  and   \$and$testcase/test76/test76.v:34$21  (\$and$testcase/test76/test76.v:34$21_Y , n2[2], n5);
  or    \$or$testcase/test76/test76.v:32$19  (n41, n2[2], n5);
  or    \$or$testcase/test76/test76.v:24$10  (\$or$testcase/test76/test76.v:24$10_Y , n20, n3[2]);
  xor   \$xor$testcase/test76/test76.v:29$16  (n31, n30, n5);
  not   \$not$testcase/test76/test76.v:34$22  (n43, \$and$testcase/test76/test76.v:34$21_Y );
  or    \$or$testcase/test76/test76.v:37$25  (n56, n40, n41);
  not   \$not$testcase/test76/test76.v:24$11  (n21, \$or$testcase/test76/test76.v:24$10_Y );
  or    \$or$testcase/test76/test76.v:30$17  (n7, n31, n4);
  or    \$or$testcase/test76/test76.v:38$26  (n57, n56, n42);
  xor   \$xor$testcase/test76/test76.v:25$12  (\$xor$testcase/test76/test76.v:25$12_Y , n21, n5);
  or    \$or$testcase/test76/test76.v:39$27  (n8, n57, n43);
  not   \$not$testcase/test76/test76.v:25$13  (n22, \$xor$testcase/test76/test76.v:25$12_Y );
  and   \$and$testcase/test76/test76.v:48$31  (n65, n31, n8);
  xor   \$xor$testcase/test76/test76.v:26$14  (n6, n22, n2[1]);
  dff g33 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  xor   \$xor$testcase/test76/test76.v:50$32  (n12, n6, n7);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
