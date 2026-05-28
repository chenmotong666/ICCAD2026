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

  wire \$and$testcase/test74/test74.v:19$3_Y , \$and$testcase/test74/test74.v:32$18_Y , \$and$testcase/test74/test74.v:36$23_Y , \$and$testcase/test74/test74.v:40$28_Y , \$or$testcase/test74/test74.v:20$5_Y , n13, n14, n15, n16, n17, n18, n19, n20, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n50, n51, n56, n57, n63, n65, n80, n81, n82;

  and   \$and$testcase/test74/test74.v:55$38  (n80, n2[0], n2[1]);
  and   \$and$testcase/test74/test74.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test74/test74.v:26$12  (n30, n2[1], n3[1]);
  and   \$and$testcase/test74/test74.v:29$15  (n40, n2[2], n3[2]);
  and   \$and$testcase/test74/test74.v:33$20  (n44, n2[2], n3[2]);
  and   \$and$testcase/test74/test74.v:37$25  (n48, n2[2], n3[2]);
  xor   \$xor$testcase/test74/test74.v:31$17  (n42, n2[2], n3[2]);
  xor   \$xor$testcase/test74/test74.v:35$22  (n46, n2[2], n3[2]);
  xor   \$xor$testcase/test74/test74.v:39$27  (n50, n2[2], n3[2]);
  not   \$not$testcase/test74/test74.v:49$42  (n63, n4);
  and   \$and$testcase/test74/test74.v:32$18  (\$and$testcase/test74/test74.v:32$18_Y , n2[2], n5);
  and   \$and$testcase/test74/test74.v:36$23  (\$and$testcase/test74/test74.v:36$23_Y , n2[2], n5);
  and   \$and$testcase/test74/test74.v:40$28  (\$and$testcase/test74/test74.v:40$28_Y , n2[2], n5);
  or    \$or$testcase/test74/test74.v:30$16  (n41, n2[2], n5);
  or    \$or$testcase/test74/test74.v:34$21  (n45, n2[2], n5);
  or    \$or$testcase/test74/test74.v:38$26  (n49, n2[2], n5);
  or    \$or$testcase/test74/test74.v:56$39  (n81, n80, n3[0]);
  or    \$or$testcase/test74/test74.v:17$2  (n14, n13, n4);
  xor   \$xor$testcase/test74/test74.v:27$13  (n31, n30, n5);
  not   \$not$testcase/test74/test74.v:32$19  (n43, \$and$testcase/test74/test74.v:32$18_Y );
  not   \$not$testcase/test74/test74.v:36$24  (n47, \$and$testcase/test74/test74.v:36$23_Y );
  not   \$not$testcase/test74/test74.v:40$29  (n51, \$and$testcase/test74/test74.v:40$28_Y );
  or    \$or$testcase/test74/test74.v:41$30  (n56, n40, n41);
  not   \$not$testcase/test74/test74.v:57$44  (n82, n81);
  not   \$not$testcase/test74/test74.v:18$40  (n15, n14);
  or    \$or$testcase/test74/test74.v:28$14  (n7, n31, n4);
  or    \$or$testcase/test74/test74.v:42$31  (n57, n56, n42);
  and   \$and$testcase/test74/test74.v:19$3  (\$and$testcase/test74/test74.v:19$3_Y , n15, n5);
  or    \$or$testcase/test74/test74.v:43$32  (n8, n57, n43);
  not   \$not$testcase/test74/test74.v:19$4  (n16, \$and$testcase/test74/test74.v:19$3_Y );
  and   \$and$testcase/test74/test74.v:52$36  (n65, n31, n8);
  or    \$or$testcase/test74/test74.v:20$5  (\$or$testcase/test74/test74.v:20$5_Y , n16, n2[1]);
  dff g37 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  not   \$not$testcase/test74/test74.v:20$6  (n17, \$or$testcase/test74/test74.v:20$5_Y );
  xor   \$xor$testcase/test74/test74.v:21$7  (n19, n17, n3[1]);
  and   \$and$testcase/test74/test74.v:23$10  (n20, n19, n2[2]);
  not   \$not$testcase/test74/test74.v:21$8  (n18, n19);
  or    \$or$testcase/test74/test74.v:24$11  (n6, n20, n3[2]);
  xor   \$xor$testcase/test74/test74.v:54$37  (n12, n6, n7);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
