module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n11,
    n12,
    n8,
    n6,
    n7,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output n11;
  output n12;
  output n8;
  output n6;
  output n7;
  output n9;
  output n10;

  wire \$and$testcase/test96/test96.v:30$15_Y , \$xor$testcase/test96/test96.v:44$25_Y , n30, n31, n40, n41, n42, n43, n56, n57;

  and   \$and$testcase/test96/test96.v:24$9  (n30, n2[1], n3[1]);
  and   \$and$testcase/test96/test96.v:27$12  (n40, n2[2], n3[2]);
  xor   \$xor$testcase/test96/test96.v:29$14  (n42, n2[2], n3[2]);
  and   \$and$testcase/test96/test96.v:30$15  (\$and$testcase/test96/test96.v:30$15_Y , n2[2], n5);
  or    \$or$testcase/test96/test96.v:28$13  (n41, n2[2], n5);
  xor   \$xor$testcase/test96/test96.v:25$10  (n31, n30, n5);
  not   \$not$testcase/test96/test96.v:30$16  (n43, \$and$testcase/test96/test96.v:30$15_Y );
  or    \$or$testcase/test96/test96.v:33$19  (n56, n40, n41);
  or    \$or$testcase/test96/test96.v:26$11  (n7, n31, n4);
  or    \$or$testcase/test96/test96.v:34$20  (n57, n56, n42);
  or    \$or$testcase/test96/test96.v:35$21  (n8, n57, n43);
  xor   \$xor$testcase/test96/test96.v:44$25  (\$xor$testcase/test96/test96.v:44$25_Y , n31, n8);
  not   \$not$testcase/test96/test96.v:44$26  (n11, \$xor$testcase/test96/test96.v:44$25_Y );

  assign n12 = n7;
  assign n6 = 1'b0;
  assign n9 = 1'b1;
  assign n10 = n4;

endmodule
