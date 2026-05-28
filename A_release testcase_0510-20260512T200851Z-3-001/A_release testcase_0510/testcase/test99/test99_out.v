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

  wire \$and$testcase/test99/test99.v:24$10_Y , \$and$testcase/test99/test99.v:33$20_Y , \$and$testcase/test99/test99.v:37$25_Y , \$and$testcase/test99/test99.v:41$30_Y , \$or$testcase/test99/test99.v:19$3_Y , \$or$testcase/test99/test99.v:25$12_Y , \$xor$testcase/test99/test99.v:20$5_Y , \$xor$testcase/test99/test99.v:53$38_Y , n13, n14, n15, n16, n17, n18, n20, n21, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n50, n51, n56, n57, n63, n80, n81, n82;

  and   \$and$testcase/test99/test99.v:58$40  (n80, n2[0], n2[1]);
  and   \$and$testcase/test99/test99.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test99/test99.v:27$14  (n30, n2[1], n3[1]);
  and   \$and$testcase/test99/test99.v:30$17  (n40, n2[2], n3[2]);
  and   \$and$testcase/test99/test99.v:34$22  (n44, n2[2], n3[2]);
  and   \$and$testcase/test99/test99.v:38$27  (n48, n2[2], n3[2]);
  xor   \$xor$testcase/test99/test99.v:32$19  (n42, n2[2], n3[2]);
  xor   \$xor$testcase/test99/test99.v:36$24  (n46, n2[2], n3[2]);
  xor   \$xor$testcase/test99/test99.v:40$29  (n50, n2[2], n3[2]);
  not   \$not$testcase/test99/test99.v:50$44  (n63, n4);
  and   \$and$testcase/test99/test99.v:33$20  (\$and$testcase/test99/test99.v:33$20_Y , n2[2], n5);
  and   \$and$testcase/test99/test99.v:37$25  (\$and$testcase/test99/test99.v:37$25_Y , n2[2], n5);
  and   \$and$testcase/test99/test99.v:41$30  (\$and$testcase/test99/test99.v:41$30_Y , n2[2], n5);
  or    \$or$testcase/test99/test99.v:31$18  (n41, n2[2], n5);
  or    \$or$testcase/test99/test99.v:35$23  (n45, n2[2], n5);
  or    \$or$testcase/test99/test99.v:39$28  (n49, n2[2], n5);
  or    \$or$testcase/test99/test99.v:59$41  (n81, n80, n3[0]);
  or    \$or$testcase/test99/test99.v:17$2  (n14, n13, n4);
  xor   \$xor$testcase/test99/test99.v:28$15  (n31, n30, n5);
  not   \$not$testcase/test99/test99.v:33$21  (n43, \$and$testcase/test99/test99.v:33$20_Y );
  not   \$not$testcase/test99/test99.v:37$26  (n47, \$and$testcase/test99/test99.v:37$25_Y );
  not   \$not$testcase/test99/test99.v:41$31  (n51, \$and$testcase/test99/test99.v:41$30_Y );
  or    \$or$testcase/test99/test99.v:42$32  (n56, n40, n41);
  not   \$not$testcase/test99/test99.v:60$46  (n82, n81);
  not   \$not$testcase/test99/test99.v:18$42  (n15, n14);
  or    \$or$testcase/test99/test99.v:29$16  (n7, n31, n4);
  or    \$or$testcase/test99/test99.v:43$33  (n57, n56, n42);
  or    \$or$testcase/test99/test99.v:19$3  (\$or$testcase/test99/test99.v:19$3_Y , n15, n5);
  or    \$or$testcase/test99/test99.v:44$34  (n8, n57, n43);
  not   \$not$testcase/test99/test99.v:19$4  (n16, \$or$testcase/test99/test99.v:19$3_Y );
  xor   \$xor$testcase/test99/test99.v:53$38  (\$xor$testcase/test99/test99.v:53$38_Y , n31, n8);
  xor   \$xor$testcase/test99/test99.v:20$5  (\$xor$testcase/test99/test99.v:20$5_Y , n16, n2[1]);
  not   \$not$testcase/test99/test99.v:53$39  (n11, \$xor$testcase/test99/test99.v:53$38_Y );
  not   \$not$testcase/test99/test99.v:20$6  (n17, \$xor$testcase/test99/test99.v:20$5_Y );
  xor   \$xor$testcase/test99/test99.v:21$7  (n18, n17, n3[1]);
  or    \$or$testcase/test99/test99.v:23$9  (n20, n18, n2[2]);
  and   \$and$testcase/test99/test99.v:24$10  (\$and$testcase/test99/test99.v:24$10_Y , n20, n3[2]);
  not   \$not$testcase/test99/test99.v:24$11  (n21, \$and$testcase/test99/test99.v:24$10_Y );
  or    \$or$testcase/test99/test99.v:25$12  (\$or$testcase/test99/test99.v:25$12_Y , n21, n5);
  not   \$not$testcase/test99/test99.v:25$13  (n6, \$or$testcase/test99/test99.v:25$12_Y );

  assign n12[0] = n6;
  assign n12[1] = n7;
  assign n12[2] = n8;
  assign n9 = 1'b1;
  assign n10 = n4;
  assign n12[3] = 1'b1;

endmodule
