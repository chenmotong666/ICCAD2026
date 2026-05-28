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

  wire \$and$testcase/test72/test72.v:21$5_Y , \$and$testcase/test72/test72.v:30$15_Y , \$and$testcase/test72/test72.v:34$20_Y , \$xor$testcase/test72/test72.v:46$28_Y , n13, n14, n15, n16, n17, n18, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n56, n57, n63, n80, n81, n82;

  and   \$and$testcase/test72/test72.v:48$31  (n80, n2[0], n2[1]);
  and   \$and$testcase/test72/test72.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test72/test72.v:24$9  (n30, n2[1], n3[1]);
  and   \$and$testcase/test72/test72.v:27$12  (n40, n2[2], n3[2]);
  and   \$and$testcase/test72/test72.v:31$17  (n44, n2[2], n3[2]);
  xor   \$xor$testcase/test72/test72.v:29$14  (n42, n2[2], n3[2]);
  xor   \$xor$testcase/test72/test72.v:33$19  (n46, n2[2], n3[2]);
  not   \$not$testcase/test72/test72.v:43$35  (n63, n4);
  and   \$and$testcase/test72/test72.v:30$15  (\$and$testcase/test72/test72.v:30$15_Y , n2[2], n5);
  and   \$and$testcase/test72/test72.v:34$20  (\$and$testcase/test72/test72.v:34$20_Y , n2[2], n5);
  or    \$or$testcase/test72/test72.v:28$13  (n41, n2[2], n5);
  or    \$or$testcase/test72/test72.v:32$18  (n45, n2[2], n5);
  or    \$or$testcase/test72/test72.v:49$32  (n81, n80, n3[0]);
  or    \$or$testcase/test72/test72.v:17$2  (n14, n13, n4);
  xor   \$xor$testcase/test72/test72.v:25$10  (n31, n30, n5);
  not   \$not$testcase/test72/test72.v:30$16  (n43, \$and$testcase/test72/test72.v:30$15_Y );
  not   \$not$testcase/test72/test72.v:34$21  (n47, \$and$testcase/test72/test72.v:34$20_Y );
  or    \$or$testcase/test72/test72.v:35$22  (n56, n40, n41);
  not   \$not$testcase/test72/test72.v:50$37  (n82, n81);
  not   \$not$testcase/test72/test72.v:18$33  (n15, n14);
  or    \$or$testcase/test72/test72.v:26$11  (n7, n31, n4);
  or    \$or$testcase/test72/test72.v:36$23  (n57, n56, n42);
  and   \$and$testcase/test72/test72.v:19$3  (n16, n15, n5);
  or    \$or$testcase/test72/test72.v:37$24  (n8, n57, n43);
  or    \$or$testcase/test72/test72.v:20$4  (n17, n16, n2[1]);
  xor   \$xor$testcase/test72/test72.v:46$28  (\$xor$testcase/test72/test72.v:46$28_Y , n31, n8);
  and   \$and$testcase/test72/test72.v:21$5  (\$and$testcase/test72/test72.v:21$5_Y , n17, n3[1]);
  not   \$not$testcase/test72/test72.v:46$29  (n11, \$xor$testcase/test72/test72.v:46$28_Y );
  not   \$not$testcase/test72/test72.v:21$6  (n18, \$and$testcase/test72/test72.v:21$5_Y );

  assign n12 = n7;
  assign n6 = 1'b0;
  assign n9 = 1'b1;
  assign n10 = n4;

endmodule
