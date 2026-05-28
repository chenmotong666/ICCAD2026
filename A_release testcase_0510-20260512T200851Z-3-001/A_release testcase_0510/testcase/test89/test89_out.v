module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
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
  output n6;
  output n7;
  output n8;
  output n12;
  output n11;
  output n9;
  output n10;

  wire \$and$testcase/test89/test89.v:29$12_Y , \$and$testcase/test89/test89.v:33$17_Y , \$and$testcase/test89/test89.v:37$22_Y , n13, n14, n15, n16, n17, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n50, n51, n56, n57, n63, n65, n80, n81, n82;

  and   \$and$testcase/test89/test89.v:52$32  (n80, n2[0], n2[1]);
  and   \$and$testcase/test89/test89.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test89/test89.v:23$6  (n30, n2[1], n3[1]);
  and   \$and$testcase/test89/test89.v:26$9  (n40, n2[2], n3[2]);
  and   \$and$testcase/test89/test89.v:30$14  (n44, n2[2], n3[2]);
  and   \$and$testcase/test89/test89.v:34$19  (n48, n2[2], n3[2]);
  xor   \$xor$testcase/test89/test89.v:28$11  (n42, n2[2], n3[2]);
  xor   \$xor$testcase/test89/test89.v:32$16  (n46, n2[2], n3[2]);
  xor   \$xor$testcase/test89/test89.v:36$21  (n50, n2[2], n3[2]);
  not   \$not$testcase/test89/test89.v:46$36  (n63, n4);
  and   \$and$testcase/test89/test89.v:29$12  (\$and$testcase/test89/test89.v:29$12_Y , n2[2], n5);
  and   \$and$testcase/test89/test89.v:33$17  (\$and$testcase/test89/test89.v:33$17_Y , n2[2], n5);
  and   \$and$testcase/test89/test89.v:37$22  (\$and$testcase/test89/test89.v:37$22_Y , n2[2], n5);
  or    \$or$testcase/test89/test89.v:27$10  (n41, n2[2], n5);
  or    \$or$testcase/test89/test89.v:31$15  (n45, n2[2], n5);
  or    \$or$testcase/test89/test89.v:35$20  (n49, n2[2], n5);
  or    \$or$testcase/test89/test89.v:53$33  (n81, n80, n3[0]);
  or    \$or$testcase/test89/test89.v:17$2  (n14, n13, n4);
  xor   \$xor$testcase/test89/test89.v:24$7  (n31, n30, n5);
  not   \$not$testcase/test89/test89.v:29$13  (n43, \$and$testcase/test89/test89.v:29$12_Y );
  not   \$not$testcase/test89/test89.v:33$18  (n47, \$and$testcase/test89/test89.v:33$17_Y );
  not   \$not$testcase/test89/test89.v:37$23  (n51, \$and$testcase/test89/test89.v:37$22_Y );
  or    \$or$testcase/test89/test89.v:38$24  (n56, n40, n41);
  not   \$not$testcase/test89/test89.v:54$38  (n82, n81);
  not   \$not$testcase/test89/test89.v:18$34  (n15, n14);
  or    \$or$testcase/test89/test89.v:25$8  (n7, n31, n4);
  or    \$or$testcase/test89/test89.v:39$25  (n57, n56, n42);
  xor   \$xor$testcase/test89/test89.v:19$3  (n16, n15, n5);
  or    \$or$testcase/test89/test89.v:40$26  (n8, n57, n43);
  and   \$and$testcase/test89/test89.v:20$4  (n17, n16, n2[1]);
  and   \$and$testcase/test89/test89.v:49$30  (n65, n31, n8);
  or    \$or$testcase/test89/test89.v:21$5  (n6, n17, n3[1]);
  dff g34 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  xor   \$xor$testcase/test89/test89.v:51$31  (n12, n6, n7);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
