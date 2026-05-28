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

  wire \$and$testcase/test78/test78.v:21$5_Y , \$and$testcase/test78/test78.v:30$15_Y , \$and$testcase/test78/test78.v:34$20_Y , \$xor$testcase/test78/test78.v:48$30_Y , n13, n15, n16, n17, n18, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n56, n57, n63, n80, n81, n82, renamed_signal;

  and   \$and$testcase/test78/test78.v:50$33  (n80, n2[0], n2[1]);
  and   renamed_gate (n13, n2[0], n3[0]);
  and   \$and$testcase/test78/test78.v:24$9  (n30, n2[1], n3[1]);
  and   \$and$testcase/test78/test78.v:27$12  (n40, n2[2], n3[2]);
  and   \$and$testcase/test78/test78.v:31$17  (n44, n2[2], n3[2]);
  and   \$and$testcase/test78/test78.v:35$22  (n48, n2[2], n3[2]);
  xor   \$xor$testcase/test78/test78.v:29$14  (n42, n2[2], n3[2]);
  xor   \$xor$testcase/test78/test78.v:33$19  (n46, n2[2], n3[2]);
  not   \$not$testcase/test78/test78.v:45$37  (n63, n4);
  and   \$and$testcase/test78/test78.v:30$15  (\$and$testcase/test78/test78.v:30$15_Y , n2[2], n5);
  and   \$and$testcase/test78/test78.v:34$20  (\$and$testcase/test78/test78.v:34$20_Y , n2[2], n5);
  or    \$or$testcase/test78/test78.v:28$13  (n41, n2[2], n5);
  or    \$or$testcase/test78/test78.v:32$18  (n45, n2[2], n5);
  or    \$or$testcase/test78/test78.v:36$23  (n49, n2[2], n5);
  or    \$or$testcase/test78/test78.v:51$34  (n81, n80, n3[0]);
  or    \$or$testcase/test78/test78.v:17$2  (renamed_signal, n13, n4);
  xor   \$xor$testcase/test78/test78.v:25$10  (n31, n30, n5);
  not   \$not$testcase/test78/test78.v:30$16  (n43, \$and$testcase/test78/test78.v:30$15_Y );
  not   \$not$testcase/test78/test78.v:34$21  (n47, \$and$testcase/test78/test78.v:34$20_Y );
  or    \$or$testcase/test78/test78.v:37$24  (n56, n40, n41);
  not   \$not$testcase/test78/test78.v:52$39  (n82, n81);
  not   \$not$testcase/test78/test78.v:18$35  (n15, renamed_signal);
  or    \$or$testcase/test78/test78.v:26$11  (n7, n31, n4);
  or    \$or$testcase/test78/test78.v:38$25  (n57, n56, n42);
  and   \$and$testcase/test78/test78.v:19$3  (n16, n15, n5);
  or    \$or$testcase/test78/test78.v:39$26  (n8, n57, n43);
  or    \$or$testcase/test78/test78.v:20$4  (n17, n16, n2[1]);
  xor   \$xor$testcase/test78/test78.v:48$30  (\$xor$testcase/test78/test78.v:48$30_Y , n31, n8);
  and   \$and$testcase/test78/test78.v:21$5  (\$and$testcase/test78/test78.v:21$5_Y , n17, n3[1]);
  not   \$not$testcase/test78/test78.v:48$31  (n11, \$xor$testcase/test78/test78.v:48$30_Y );
  not   \$not$testcase/test78/test78.v:21$6  (n18, \$and$testcase/test78/test78.v:21$5_Y );

  assign n12 = n7;
  assign n6 = 1'b0;
  assign n9 = 1'b1;
  assign n10 = n4;

endmodule
